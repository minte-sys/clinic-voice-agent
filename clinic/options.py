"""Per-conversation options chosen on the web page (talk or type, language)."""
from __future__ import annotations

import json

from .speech import LANGUAGES

MODES = ("voice", "text")


def job_options(metadata: str | None, default_language: str = "en") -> tuple[str, str, str | None]:
    """Read the web page's choice from LiveKit job metadata.

    Returns (mode, start_language, chosen_language). chosen_language is None when
    the person did not pick one, for example in sessions from the Agent Console.
    Anything unexpected falls back to a voice call in the default language.
    """
    try:
        data = json.loads(metadata) if metadata else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    mode = "text" if data.get("mode") == "text" else "voice"
    language = data.get("language")
    chosen = language if isinstance(language, str) and language in LANGUAGES else None
    return mode, chosen or default_language, chosen
