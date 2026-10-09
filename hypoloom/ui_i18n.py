"""Presentation-only translations; research records and protocol values stay original."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


def format_local_time(value: object) -> str:
    """Display an epoch in the observer process's local zone, with its offset."""
    if type(value) not in (int, float):
        return "unknown"
    try:
        return datetime.fromtimestamp(value, timezone.utc).astimezone().isoformat(sep=" ", timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return "unknown"


@lru_cache(maxsize=1)
def messages() -> dict[str, str]:
    return json.loads(Path(__file__).with_name("web_observer_assets").joinpath(
        "messages.json").read_text(encoding="utf-8"))


def translate(message: str, *values: object, language: str = "en") -> str:
    template = messages().get(message, message) if language == "zh-CN" else message
    return template.format(*values) if values else template


def tr(message: str, *values: object) -> str:
    """Use the current Textual app, never a process-global language setting."""
    from textual._context import active_app
    try:
        language = getattr(active_app.get(), "ui_language", "en")
    except LookupError:
        language = "en"
    return translate(message, *values, language=language)
