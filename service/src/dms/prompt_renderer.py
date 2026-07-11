"""Prompt template loading and rendering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .settings import SERVICE_DIR, Settings


@dataclass(frozen=True)
class RenderedPrompt:
    """Rendered prompt text with provenance metadata."""

    text: str
    source_path: Path
    version: str
    sha256: str


DEFAULT_ISSUE_PROMPT_PATH = SERVICE_DIR / "config" / "prompts" / "issue_classifier_v1.txt"
LEGACY_ISSUE_PROMPT_NAME = "system_prompt.txt"
ISSUE_PROMPT_VARIABLES = (
    "minor_order_json",
    "label_defs",
    "hints_json",
    "brand_json",
    "input_json",
)


def _load_issue_template(settings: Settings) -> tuple[Path, str]:
    legacy_override = settings.keyword_dir / LEGACY_ISSUE_PROMPT_NAME
    if legacy_override.is_file():
        return legacy_override, legacy_override.read_text(encoding="utf-8")
    return DEFAULT_ISSUE_PROMPT_PATH, DEFAULT_ISSUE_PROMPT_PATH.read_text(encoding="utf-8")


def render_template(template: str, variables: dict[str, str]) -> str:
    """Render a prompt template by replacing explicit `{name}` placeholders."""
    rendered = template
    missing = []
    for name in ISSUE_PROMPT_VARIABLES:
        placeholder = "{" + name + "}"
        value = variables.get(name)
        if value is None:
            missing.append(name)
            continue
        rendered = rendered.replace(placeholder, value)
    if missing:
        raise ValueError("Missing prompt variables: " + ", ".join(missing))
    return rendered.strip()


def render_issue_classifier_prompt(
    settings: Settings,
    variables: dict[str, str],
) -> RenderedPrompt:
    """Render the issue classifier prompt and compute provenance metadata."""
    source_path, template = _load_issue_template(settings)
    text = render_template(template, variables)
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return RenderedPrompt(
        text=text,
        source_path=source_path,
        version=source_path.stem,
        sha256=digest,
    )
