"""Gateway LLM cho chat: JSON + stream (design b01 D3, b04 D9).

Bọc ``GeminiClient`` với retry ngắn riêng của chat, model riêng của chat, và ghi
``UsageTracker`` **đúng một lần** cho mỗi lần gọi — kể cả khi lỗi hoặc bị huỷ giữa chừng.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from ...gemini_client import GeminiClient
from ...settings import Settings
from ...usage_tracker import calculate_cost
from .budget import request_context
from .budget.usage_ledger import ChatUsageLedger
from .types import LLMResult

logger = logging.getLogger("dms-chat-llm")

CHAT_LLM_MAX_RETRY = 1
CHAT_LLM_TIMEOUT_SECONDS = 20.0
# Backoff riêng của chat: pipeline dùng base_wait 4s, quá lâu cho một lượt hỏi (b04 D5).
CHAT_LLM_BASE_WAIT_SECONDS = 0.5


class JsonGenerator(Protocol):
    def generate_json(self, prompt: str, temperature: float = 0.0) -> Any: ...


class UsageRecorder(Protocol):
    def record(
        self,
        *,
        model: str,
        call_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        duration_ms: int | None = None,
        success: bool = True,
        job_id: str | None = None,
    ) -> None: ...


def chat_llm_settings(
    settings: Settings,
    *,
    max_retry: int = CHAT_LLM_MAX_RETRY,
    timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
    base_wait: float = CHAT_LLM_BASE_WAIT_SECONDS,
    model: str | None = None,
) -> Settings:
    """Bản sao settings cho chat; không đổi settings mà pipeline phân loại đang dùng."""
    update: dict[str, Any] = {
        # ``Settings.max_retry`` của pipeline là **tổng số lần gọi**, còn ``max_retry`` của chat
        # là số lần **thử lại** (như ``GeminiStream``): 0 vẫn phải gọi một lần.
        "max_retry": max(0, int(max_retry)) + 1,
        "base_wait": base_wait,
        "gemini_timeout_seconds": min(float(settings.gemini_timeout_seconds), timeout_seconds),
    }
    if model:
        update["gemini_model"] = model
    return settings.model_copy(update=update)


def _parse_pricing(raw: str) -> dict[str, Any]:
    try:
        pricing = json.loads(raw or "{}")
    except json.JSONDecodeError:
        logger.warning("chat_llm_pricing_invalid")
        return {}
    return pricing if isinstance(pricing, dict) else {}


class LLMStream:
    """Bọc ``GeminiStream`` và bảo đảm usage được ghi đúng một lần (design b04 D9).

    Ghi ở đúng một trong ba điểm: kết thúc bình thường, lỗi, hoặc ``close()``.
    """

    def __init__(self, inner: Any, *, on_finish: Callable[[Mapping[str, int], bool], None]) -> None:
        self._inner = inner
        self._on_finish = on_finish
        self._recorded = False
        self._got_text = False

    # ── Iterator ──

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        try:
            text = next(self._inner)
        except StopIteration:
            self._record(success=True)
            raise
        except BaseException:
            # Lỗi trước đoạn đầu tiên tính là thất bại; sau đó coi như đã phục vụ được người dùng.
            self._record(success=self._got_text)
            raise
        self._got_text = True
        return text

    # ── Huỷ ──

    def close(self) -> None:
        self._inner.close()
        self._record(success=self._got_text)

    def __enter__(self) -> LLMStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── Metadata đọc thẳng từ GeminiStream ──

    @property
    def usage(self) -> Mapping[str, int]:
        return self._inner.usage

    @property
    def finish_reason(self) -> str | None:
        return self._inner.finish_reason

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def first_chunk_ms(self) -> int | None:
        return self._inner.first_chunk_ms

    @property
    def total_ms(self) -> int | None:
        return self._inner.total_ms

    @property
    def cancelled(self) -> bool:
        return bool(self._inner.cancelled)

    def _record(self, *, success: bool) -> None:
        if self._recorded:
            return
        self._recorded = True
        self._on_finish(self._inner.usage or {}, success)


class GeminiChatGateway:
    """Cài đặt ``LLMClient`` của chat trên ``GeminiClient``."""

    def __init__(
        self,
        settings: Settings,
        *,
        usage_tracker: UsageRecorder | None = None,
        gemini: Any | None = None,
        max_retry: int | None = None,
        timeout_seconds: float = CHAT_LLM_TIMEOUT_SECONDS,
        ledger: ChatUsageLedger | None = None,
    ) -> None:
        retry = (
            max_retry
            if max_retry is not None
            else getattr(settings, "chat_llm_max_retry", CHAT_LLM_MAX_RETRY)
        )
        chat_model = (getattr(settings, "chat_gemini_model", "") or "").strip()
        self.settings = chat_llm_settings(
            settings,
            max_retry=int(retry),
            timeout_seconds=timeout_seconds,
            model=chat_model or None,
        )
        self._gemini: Any = gemini if gemini is not None else GeminiClient(self.settings)
        self._usage = usage_tracker
        # Sổ usage theo user (b11 D8); chỉ ghi khi lượt chat đã đặt ChatRequestContext.
        self.ledger = ledger
        self._pricing = _parse_pricing(settings.gemini_model_pricing)

    @property
    def model(self) -> str:
        return self.settings.gemini_model

    # ── JSON ──

    def generate_json(
        self, prompt: str, *, call_type: str, system_instruction: str | None = None
    ) -> LLMResult:
        started = time.monotonic()
        extra: dict[str, Any] = {}
        if system_instruction is not None:
            extra["system_instruction"] = system_instruction
        try:
            response = self._gemini.generate_json(prompt, temperature=0.0, **extra)
        except Exception:
            self._record(call_type, {}, _elapsed_ms(started), success=False)
            raise
        latency_ms = _elapsed_ms(started)
        usage = {str(k): int(v or 0) for k, v in (getattr(response, "usage", None) or {}).items()}
        self._record(call_type, usage, latency_ms, success=True)
        return LLMResult(
            text=str(getattr(response, "text", "") or ""),
            usage=usage,
            latency_ms=latency_ms,
            model=self.model,
        )

    # ── Stream ──

    def stream(
        self,
        prompt: str,
        *,
        call_type: str,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMStream:
        started = time.monotonic()
        try:
            inner = self._gemini.stream(
                prompt,
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        except Exception:
            self._record(call_type, {}, _elapsed_ms(started), success=False)
            raise

        def on_finish(usage: Mapping[str, int], success: bool) -> None:
            self._record(call_type, dict(usage), _elapsed_ms(started), success=success)

        return LLMStream(inner, on_finish=on_finish)

    # ── Usage ──

    def _record(
        self, call_type: str, usage: Mapping[str, int], duration_ms: int, *, success: bool
    ) -> None:
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))
        cost = calculate_cost(self.model, prompt_tokens, completion_tokens, self._pricing)
        if self._usage is not None:
            try:
                self._usage.record(
                    model=self.model,
                    call_type=call_type,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=cost,
                    duration_ms=duration_ms,
                    success=success,
                    job_id=None,
                )
            except Exception:  # ghi usage lỗi không được làm hỏng lượt hỏi
                logger.warning("chat_usage_record_failed", extra={"call_type": call_type})

        context = request_context.current()
        if self.ledger is None or context is None:
            return  # script, eval: không thuộc lượt chat của user nào
        try:
            self.ledger.record(
                username=context.username,
                request_id=context.request_id,
                session_id=context.session_id,
                call_type=call_type,
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
                success=success,
                at=datetime.now(UTC),
            )
        except Exception:
            logger.warning("chat_usage_ledger_failed", extra={"call_type": call_type})


# Tên cũ của b01 vẫn dùng được (spec chat-llm-gateway).
GeminiJsonClient = GeminiChatGateway


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
