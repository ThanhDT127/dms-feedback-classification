"""SQL Guard dựa trên cây cú pháp (sqlglot) cho Pattern 2 (spec ``chat-sql-guard``, design b09 D4–D5).

Lớp phòng thủ thứ hai sau view đã áp phạm vi + authorizer của Dev A. SQL gửi executor là chuỗi
**dựng lại** từ cây đã kiểm, không phải chuỗi LLM trả về. **Không gọi LLM.**
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from ...settings import Settings
from ..ai.semantic_schema import (
    ALIAS_PATTERN,
    DATE_COLUMN,
    ISSUE_MEASURE_HINT,
    UNIT_COLUMN,
    VIEWS,
    all_forbidden_columns,
    categorical_columns,
)

logger = logging.getLogger("dms-chat-sql-guard")

MAX_ERROR_DETAIL = 300


class SqlErrorCode(StrEnum):
    SQL_PARSE = "SQL_PARSE"
    SQL_MULTI_STATEMENT = "SQL_MULTI_STATEMENT"
    SQL_NOT_SELECT = "SQL_NOT_SELECT"
    SQL_COMMENT = "SQL_COMMENT"
    SQL_TABLE_FORBIDDEN = "SQL_TABLE_FORBIDDEN"
    SQL_COLUMN_UNKNOWN = "SQL_COLUMN_UNKNOWN"
    SQL_COLUMN_FORBIDDEN = "SQL_COLUMN_FORBIDDEN"
    SQL_FUNCTION_FORBIDDEN = "SQL_FUNCTION_FORBIDDEN"
    SQL_TOO_COMPLEX = "SQL_TOO_COMPLEX"
    SQL_LITERAL_UNKNOWN_VALUE = "SQL_LITERAL_UNKNOWN_VALUE"
    SQL_DATE_MISMATCH = "SQL_DATE_MISMATCH"
    SQL_UNIT_OUT_OF_SCOPE = "SQL_UNIT_OUT_OF_SCOPE"
    SQL_LIKE_NOT_ALLOWED = "SQL_LIKE_NOT_ALLOWED"
    SQL_MEASURE_MISMATCH = "SQL_MEASURE_MISMATCH"
    SQL_ALIAS_INVALID = "SQL_ALIAS_INVALID"


# Không sửa được: dừng vòng lặp ngay (D6).
NON_REPAIRABLE = frozenset(
    {
        SqlErrorCode.SQL_TABLE_FORBIDDEN,
        SqlErrorCode.SQL_COLUMN_FORBIDDEN,
        SqlErrorCode.SQL_UNIT_OUT_OF_SCOPE,
    }
)
SECURITY_CODES = frozenset({SqlErrorCode.SQL_TABLE_FORBIDDEN, SqlErrorCode.SQL_COLUMN_FORBIDDEN})

# Lớp sqlglot của các hàm được phép (tên SQLite → lớp: ifnull→Coalesce, substr→Substring,
# strftime→TimeToStr, date→Date, julianday→TsOrDsToTimestamp/Anonymous).
ALLOWED_FUNCTIONS: tuple[type[exp.Expression], ...] = (
    exp.Count,
    exp.Sum,
    exp.Avg,
    exp.Min,
    exp.Max,
    exp.Round,
    exp.Coalesce,
    exp.Nullif,
    exp.Lower,
    exp.Upper,
    exp.Trim,
    exp.Substring,
    exp.Length,
    exp.Date,
    exp.TimeToStr,
    exp.TsOrDsToTimestamp,
    exp.Cast,
    exp.Case,
    exp.If,
)
ALLOWED_ANONYMOUS = frozenset({"julianday"})
STRUCTURAL_FUNCTIONS: tuple[type[exp.Expression], ...] = (exp.Connector,)
FORBIDDEN_ANONYMOUS = frozenset(
    {"load_extension", "readfile", "writefile", "fts3_tokenizer", "zipfile"}
)
COMPARISONS: tuple[type[exp.Expression], ...] = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


class SqlGuardError(Exception):
    def __init__(self, code: SqlErrorCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail[:MAX_ERROR_DETAIL]

    @property
    def repairable(self) -> bool:
        return self.code not in NON_REPAIRABLE


@dataclass(frozen=True)
class SqlGuardConfig:
    max_rows: int = 200
    max_joins: int = 2
    max_subquery_depth: int = 3
    json_extract_enabled: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> SqlGuardConfig:
        patterns = {p.strip() for p in settings.chat_enabled_patterns.split(",")}
        return cls(
            max_rows=int(settings.chat_sql_max_rows),
            max_joins=int(settings.chat_sql_max_joins),
            max_subquery_depth=int(settings.chat_sql_max_subquery_depth),
            json_extract_enabled="json_extract" in patterns,
        )


@dataclass(frozen=True)
class SqlGuardContext:
    """Dữ kiện của lượt: phạm vi (``None`` = admin), giá trị hợp lệ theo khoá, ngày đã giải."""

    scope_units: tuple[str, ...] | None = None
    valid_values: Mapping[str, Sequence[str]] = field(default_factory=dict)
    dates: frozenset[str] = frozenset()
    raw_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GuardedSql:
    sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    limit: int
    output_aliases: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class SqlGuard:
    def __init__(self, config: SqlGuardConfig | None = None) -> None:
        self.config = config or SqlGuardConfig()

    def check(self, sql: str, context: SqlGuardContext | None = None) -> GuardedSql:
        ctx = context or SqlGuardContext()
        try:
            return self._check(sql or "", ctx)
        except SqlGuardError as error:
            if error.code in SECURITY_CODES:
                logger.warning(
                    "sql_guard_security_block",
                    extra={
                        "code": error.code.value,
                        "detail": error.detail,
                        "sql": (sql or "")[:2000],
                    },
                )
            else:
                logger.info(
                    "sql_guard_rejected", extra={"code": error.code.value, "detail": error.detail}
                )
            raise

    # ── Các luật theo thứ tự D5 ──

    def _check(self, sql: str, ctx: SqlGuardContext) -> GuardedSql:
        # 1–2: parse, một câu
        try:
            tokens = sqlglot.tokenize(sql, read="sqlite")
            statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
        except (ParseError, TokenError) as exc:
            raise SqlGuardError(SqlErrorCode.SQL_PARSE, str(exc)) from exc
        if not statements:
            raise SqlGuardError(SqlErrorCode.SQL_PARSE, "không có câu lệnh")
        if len(statements) != 1:
            raise SqlGuardError(SqlErrorCode.SQL_MULTI_STATEMENT, f"có {len(statements)} câu lệnh")
        tree = statements[0]

        # 6 (sớm): PRAGMA / ATTACH / DETACH / lệnh lạ
        if isinstance(tree, (exp.Pragma, exp.Attach, exp.Detach, exp.Command)):
            raise SqlGuardError(
                SqlErrorCode.SQL_TABLE_FORBIDDEN, f"lệnh {type(tree).__name__.upper()}"
            )
        # 3: gốc SELECT, không set operation
        if not isinstance(tree, exp.Select):
            raise SqlGuardError(SqlErrorCode.SQL_NOT_SELECT, f"gốc là {type(tree).__name__}")
        if any(tree.find_all(exp.SetOperation)):
            raise SqlGuardError(SqlErrorCode.SQL_NOT_SELECT, "không dùng UNION/INTERSECT/EXCEPT")
        if any(tree.find_all(exp.Pragma, exp.Attach, exp.Detach, exp.Command, exp.DML)):
            raise SqlGuardError(
                SqlErrorCode.SQL_TABLE_FORBIDDEN, "lệnh không phải SELECT bên trong"
            )
        # 4: comment trong chuỗi gốc (kể cả "--" rỗng ở cuối)
        if any(token.comments for token in tokens):
            raise SqlGuardError(SqlErrorCode.SQL_COMMENT, "không dùng comment")

        # 9: CTE đệ quy
        with_ = tree.args.get("with")
        if with_ is not None and with_.args.get("recursive"):
            raise SqlGuardError(SqlErrorCode.SQL_TOO_COMPLEX, "không dùng WITH RECURSIVE")
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}

        # 5–6: bảng
        view_aliases: dict[str, str] = {}
        tables: list[str] = []
        for table in tree.find_all(exp.Table):
            name = table.name.lower()
            if table.args.get("db") or table.args.get("catalog"):
                raise SqlGuardError(
                    SqlErrorCode.SQL_TABLE_FORBIDDEN, f"bảng có schema: {table.sql()}"
                )
            if name.startswith("sqlite_"):
                raise SqlGuardError(SqlErrorCode.SQL_TABLE_FORBIDDEN, f"bảng hệ thống {name}")
            if name in cte_names:
                continue
            if name not in VIEWS:
                raise SqlGuardError(
                    SqlErrorCode.SQL_TABLE_FORBIDDEN, f"bảng {name} không được phép"
                )
            tables.append(name)
            view_aliases[(table.alias or name).lower()] = name
        for node in tree.find_all(exp.Anonymous):
            if str(node.name).lower() in FORBIDDEN_ANONYMOUS:
                raise SqlGuardError(SqlErrorCode.SQL_TABLE_FORBIDDEN, f"hàm {node.name}")

        # D9: json_extract(raw_data_json, '$."key"') khi Pattern 4 bật
        self._check_json_extract(tree, ctx)
        # 7: cột
        columns = self._check_columns(tree, tables, view_aliases, cte_names)
        self._check_star(tree)
        # 8: hàm
        self._check_functions(tree)
        # 9: độ phức tạp
        self._check_complexity(tree)
        # 10: literal
        self._check_literals(tree, ctx)
        # 11: alias + độ đo
        aliases = self._check_aliases(tree)
        # 12: LIMIT
        limit = self._apply_limit(tree)
        return GuardedSql(
            sql=tree.sql(dialect="sqlite"),
            tables=tuple(dict.fromkeys(tables)),
            columns=tuple(dict.fromkeys(columns)),
            limit=limit,
            output_aliases=aliases,
        )

    def _check_columns(
        self,
        tree: exp.Select,
        tables: Sequence[str],
        view_aliases: Mapping[str, str],
        cte_names: Iterable[str],
    ) -> list[str]:
        forbidden = all_forbidden_columns()
        view_columns = {name: set(VIEWS[name].columns) for name in set(tables)}
        known_any = set().union(*view_columns.values()) if view_columns else set()
        derived_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
        for cte in tree.find_all(exp.CTE):
            cte_alias = cte.args.get("alias")
            if cte_alias is not None:
                derived_aliases |= {c.name.lower() for c in cte_alias.find_all(exp.Identifier)}
        subquery_aliases = {s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
        passthrough = {n.lower() for n in cte_names} | subquery_aliases
        used: list[str] = []
        for column in tree.find_all(exp.Column):
            name = column.name.lower()
            if self._json_extract_allowed(column):
                used.append(name)
                continue
            if name in forbidden:
                raise SqlGuardError(SqlErrorCode.SQL_COLUMN_FORBIDDEN, f"cột {name} bị cấm")
            qualifier = column.table.lower() if column.table else ""
            if qualifier:
                if qualifier in view_aliases:
                    if name not in view_columns[view_aliases[qualifier]]:
                        raise SqlGuardError(
                            SqlErrorCode.SQL_COLUMN_UNKNOWN,
                            f"cột {name} không có trong {view_aliases[qualifier]}",
                        )
                elif qualifier not in passthrough:
                    raise SqlGuardError(
                        SqlErrorCode.SQL_COLUMN_UNKNOWN, f"bảng/bí danh {qualifier} không rõ"
                    )
            elif name not in known_any and name not in derived_aliases:
                raise SqlGuardError(
                    SqlErrorCode.SQL_COLUMN_UNKNOWN, f"cột {name} không có trong schema"
                )
            used.append(name)
        return used

    def _json_extract_allowed(self, column: exp.Column) -> bool:
        if not self.config.json_extract_enabled or column.name.lower() != "raw_data_json":
            return False
        parent = column.parent
        return (
            isinstance(parent, exp.Anonymous)
            and str(parent.name).lower() == "json_extract"
            and bool(parent.expressions)
            and parent.expressions[0] is column
        )

    def _check_json_extract(self, tree: exp.Select, ctx: SqlGuardContext) -> None:
        if not self.config.json_extract_enabled:
            return
        for node in list(tree.find_all(exp.JSONExtract)):
            column, path = node.this, node.expression
            keys = (
                [p.this for p in path.expressions if isinstance(p, exp.JSONPathKey)]
                if isinstance(path, exp.JSONPath)
                else []
            )
            shape_ok = (
                isinstance(column, exp.Column)
                and column.name.lower() == "raw_data_json"
                and isinstance(path, exp.JSONPath)
                and len(path.expressions) == 2
                and len(keys) == 1
            )
            if not shape_ok:
                raise SqlGuardError(
                    SqlErrorCode.SQL_FUNCTION_FORBIDDEN,
                    "chỉ dùng json_extract(raw_data_json, '$.\"<key>\"')",
                )
            key = str(keys[0])
            if key not in ctx.raw_keys:
                raise SqlGuardError(
                    SqlErrorCode.SQL_LITERAL_UNKNOWN_VALUE,
                    f"key '{key[:60]}' không có trong dữ liệu",
                )
            # Dựng lại thành hàm json_extract (sqlglot mặc định in toán tử -> có ngữ nghĩa khác).
            quoted = key.replace('"', '""')
            node.replace(
                exp.Anonymous(
                    this="json_extract",
                    expressions=[exp.column("raw_data_json"), exp.Literal.string(f'$."{quoted}"')],
                )
            )

    @staticmethod
    def _check_star(tree: exp.Select) -> None:
        cte_selects = {
            id(select) for cte in tree.find_all(exp.CTE) for select in cte.find_all(exp.Select)
        }
        for star in tree.find_all(exp.Star):
            if isinstance(star.parent, exp.Count):
                continue
            select = star.find_ancestor(exp.Select)
            if select is None or id(select) not in cte_selects:
                raise SqlGuardError(
                    SqlErrorCode.SQL_COLUMN_UNKNOWN, "chỉ dùng SELECT * bên trong CTE"
                )

    def _check_functions(self, tree: exp.Select) -> None:
        for node in tree.find_all(exp.Func):
            if isinstance(node, STRUCTURAL_FUNCTIONS):
                continue
            if isinstance(node, exp.Anonymous):
                name = str(node.name).lower()
                if name in ALLOWED_ANONYMOUS or (
                    name == "json_extract" and self.config.json_extract_enabled
                ):
                    continue
                raise SqlGuardError(
                    SqlErrorCode.SQL_FUNCTION_FORBIDDEN, f"hàm {node.name} không được phép"
                )
            if not isinstance(node, ALLOWED_FUNCTIONS):
                raise SqlGuardError(
                    SqlErrorCode.SQL_FUNCTION_FORBIDDEN, f"hàm {node.sql_name()} không được phép"
                )

    def _check_complexity(self, tree: exp.Select) -> None:
        depth = 0
        for select in tree.find_all(exp.Select):
            level = 0
            parent = select.parent
            while parent is not None:
                if isinstance(parent, exp.Select):
                    level += 1
                parent = parent.parent
            depth = max(depth, level)
        if depth > self.config.max_subquery_depth:
            raise SqlGuardError(SqlErrorCode.SQL_TOO_COMPLEX, f"subquery lồng {depth} tầng")
        joins = sum(1 for _ in tree.find_all(exp.Join))
        if joins > self.config.max_joins:
            raise SqlGuardError(SqlErrorCode.SQL_TOO_COMPLEX, f"{joins} phép nối")

    def _check_literals(self, tree: exp.Select, ctx: SqlGuardContext) -> None:
        categorical = categorical_columns()
        for node in tree.find_all(*COMPARISONS, exp.In, exp.Between, exp.Like, exp.ILike):
            column = (
                node.this.find(exp.Column)
                if isinstance(node, (exp.In, exp.Between, exp.Like, exp.ILike))
                else None
            )
            others: list[exp.Expression] = []
            if isinstance(node, exp.Binary) and isinstance(node, COMPARISONS):
                left, right = node.left, node.right
                if isinstance(right, exp.Column) or (
                    right.find(exp.Column) and not left.find(exp.Column)
                ):
                    left, right = right, left
                column = left.find(exp.Column) if not isinstance(left, exp.Column) else left
                others = [right]
            elif isinstance(node, exp.In):
                others = list(node.expressions)
            elif isinstance(node, exp.Between):
                others = [node.args["low"], node.args["high"]]
            elif isinstance(node, (exp.Like, exp.ILike)):
                others = [node.expression]
            if column is None:
                continue
            name = column.name.lower()
            literals = [o.this for o in others if isinstance(o, exp.Literal) and o.is_string]

            if isinstance(node, (exp.Like, exp.ILike)):
                col = next((v.columns.get(name) for v in VIEWS.values() if name in v.columns), None)
                if col is None or not col.like_allowed:
                    raise SqlGuardError(
                        SqlErrorCode.SQL_LIKE_NOT_ALLOWED, f"không dùng LIKE trên {name}"
                    )
                continue
            if name == UNIT_COLUMN and ctx.scope_units is not None:
                allowed = {u.casefold() for u in ctx.scope_units}
                for value in literals:
                    if value.casefold() not in allowed:
                        raise SqlGuardError(
                            SqlErrorCode.SQL_UNIT_OUT_OF_SCOPE, "đơn vị ngoài phạm vi"
                        )
            if name == DATE_COLUMN:
                for value in literals:
                    if value not in ctx.dates:
                        raise SqlGuardError(
                            SqlErrorCode.SQL_DATE_MISMATCH,
                            f"ngày {value} không thuộc ngày đã giải ({', '.join(sorted(ctx.dates)) or 'không có'})",
                        )
            col_spec = categorical.get(name)
            if col_spec is not None and col_spec.values:
                valid = ctx.valid_values.get(col_spec.values)
                if valid:
                    lookup = {str(v).casefold() for v in valid}
                    for value in literals:
                        if value.casefold() not in lookup:
                            raise SqlGuardError(
                                SqlErrorCode.SQL_LITERAL_UNKNOWN_VALUE,
                                f"giá trị '{value[:60]}' không có trong cột {name}",
                            )

    @staticmethod
    def _check_aliases(tree: exp.Select) -> tuple[str, ...]:
        aliases: list[str] = []
        for alias in tree.find_all(exp.Alias):
            name = alias.alias
            if not name:
                continue
            if not ALIAS_PATTERN.match(name):
                raise SqlGuardError(
                    SqlErrorCode.SQL_ALIAS_INVALID,
                    f"alias '{name[:40]}' chỉ gồm chữ thường, số và _",
                )
            if ISSUE_MEASURE_HINT in name and not _is_distinct_issue_count(alias.this):
                raise SqlGuardError(
                    SqlErrorCode.SQL_MEASURE_MISMATCH,
                    f"{name} phải là COUNT(DISTINCT issue_code)",
                )
            if alias.parent is tree:
                aliases.append(name)
        return tuple(aliases)

    def _apply_limit(self, tree: exp.Select) -> int:
        cap = self.config.max_rows
        limit = tree.args.get("limit")
        value: int | None = None
        if limit is not None:
            expression = limit.expression
            if isinstance(expression, exp.Literal) and expression.is_int:
                value = int(expression.this)
        final = cap if value is None else min(value, cap)
        tree.limit(final, copy=False)
        return final


def _is_distinct_issue_count(expression: exp.Expression) -> bool:
    if not isinstance(expression, exp.Count):
        return False
    inner = expression.this
    if not isinstance(inner, exp.Distinct):
        return False
    columns = inner.expressions
    return (
        len(columns) == 1
        and isinstance(columns[0], exp.Column)
        and columns[0].name.lower() == "issue_code"
    )


DATE_FILTER_KEYS = ("date_from", "date_to", "compare_from", "compare_to")


def sql_valid_values(metadata_values: Mapping[str, Sequence[str]]) -> dict[str, Sequence[str]]:
    """Giá trị hợp lệ theo khoá ``Col.values``: metadata của Dev A + hằng của contract."""
    from ..contract import CLASSIFICATION_LABELS, SENTIMENT_LABELS

    values: dict[str, Sequence[str]] = {k: list(v) for k, v in metadata_values.items()}
    values.setdefault("sentiments", list(SENTIMENT_LABELS))
    values.setdefault("labels", list(CLASSIFICATION_LABELS))
    return values


def context_for(
    filters: Mapping[str, object],
    *,
    scope_units: Sequence[str] | None,
    metadata_values: Mapping[str, Sequence[str]],
    raw_keys: Iterable[str] = (),
) -> SqlGuardContext:
    return SqlGuardContext(
        scope_units=None if scope_units is None else tuple(scope_units),
        valid_values=sql_valid_values(metadata_values),
        dates=frozenset(str(filters[k]) for k in DATE_FILTER_KEYS if filters.get(k)),
        raw_keys=frozenset(raw_keys),
    )
