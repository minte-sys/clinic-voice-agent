"""A native voice for each language, and a script-based language check for replies.

Why: one voice speaking every language keeps the accent of its own language, so an
English voice reading Arabic sounds foreign. Each language gets a voice recorded by
a native speaker of that language, and the agent switches voice whenever the
language of its reply changes.

The defaults are Inworld voices (model `inworld/inworld-tts-2` on LiveKit Inference).
Override any of them in .env.local with TTS_VOICE_EN, TTS_VOICE_AR, TTS_VOICE_HI,
TTS_VOICE_ZH or TTS_VOICE_RU, for example to use a male voice or another provider.
"""
from __future__ import annotations

from collections.abc import Mapping

from .speech import LANGUAGES

# Female voices, one per language, all native speakers. Male alternatives in comments.
INWORLD_VOICES = {
    "en": "Ashley",
    "ar": "Nour",       # Saudi Arabic. Male: Omar
    "hi": "Riya",       # Male: Manoj
    "zh": "Xinyi",      # Mandarin. Others: Xiaoyin, Jing, Yichen (male)
    "ru": "Svetlana",   # Others: Elena, Dmitry (male), Nikolai (male)
}


def voice_for(language: str, model: str, env: Mapping[str, str]) -> str | None:
    """The voice to use for `language`, or None to let the provider choose.

    Order: TTS_VOICE_<LANG> from the environment, then the built-in Inworld voice
    (only when the model is an Inworld model), then the single TTS_VOICE setting.
    """
    specific = env.get(f"TTS_VOICE_{language.upper()}", "").strip()
    if specific:
        return specific
    if model.startswith("inworld/") and language in INWORLD_VOICES:
        return INWORLD_VOICES[language]
    return env.get("TTS_VOICE", "").strip() or None


def detect_language(text: str) -> str | None:
    """Guess the language of a reply from its writing system.

    Arabic script -> ar, Devanagari -> hi, Chinese characters -> zh,
    Cyrillic -> ru, Latin letters -> en. Returns None when there are no letters.
    Counts letters, so an Arabic sentence with one English word is still Arabic.
    """
    counts = {"ar": 0, "hi": 0, "zh": 0, "ru": 0, "en": 0}
    for ch in text:
        code = ord(ch)
        if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0xFB50 <= code <= 0xFEFF:
            counts["ar"] += 1
        elif 0x0900 <= code <= 0x097F:
            counts["hi"] += 1
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            counts["zh"] += 2          # one character carries about a syllable
        elif 0x0400 <= code <= 0x04FF:
            counts["ru"] += 1
        elif ch.isascii() and ch.isalpha():
            counts["en"] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else None


assert set(INWORLD_VOICES) == set(LANGUAGES), "every supported language needs a voice"
