"""Native voice per language, and language detection of replies by script."""
import pytest

from clinic.speech import LANGUAGES
from clinic.voices import INWORLD_VOICES, detect_language, voice_for


def test_every_language_has_a_native_inworld_voice():
    assert set(INWORLD_VOICES) == set(LANGUAGES)
    assert len(set(INWORLD_VOICES.values())) == len(LANGUAGES)      # no voice shared


@pytest.mark.parametrize("language, voice", list(INWORLD_VOICES.items()))
def test_inworld_model_uses_builtin_voices(language, voice):
    assert voice_for(language, "inworld/inworld-tts-2", {}) == voice


def test_env_override_wins_per_language():
    env = {"TTS_VOICE_AR": "Omar", "TTS_VOICE": "Generic"}
    assert voice_for("ar", "inworld/inworld-tts-2", env) == "Omar"
    assert voice_for("hi", "inworld/inworld-tts-2", env) == "Riya"


def test_other_providers_fall_back_to_single_voice_or_none():
    assert voice_for("ar", "cartesia/sonic-3", {"TTS_VOICE": "abc"}) == "abc"
    assert voice_for("ar", "cartesia/sonic-3", {}) is None
    assert voice_for("ar", "cartesia/sonic-3", {"TTS_VOICE_AR": " xyz "}) == "xyz"


@pytest.mark.parametrize("text, expected", [
    ("Which specialty do you need?", "en"),
    ("أي تخصص تحتاج؟", "ar"),
    ("تبغى موعد عند dermatology بكرة؟", "ar"),       # Arabic with one English word
    ("आपको किस दिन अपॉइंटमेंट चाहिए?", "hi"),
    ("您需要哪个科室？", "zh"),
    ("Какой врач вам нужен?", "ru"),
    ("Dr. Haddad, 10:30?", "en"),
    ("123 456", None),
    ("", None),
])
def test_detect_language(text, expected):
    assert detect_language(text) == expected
