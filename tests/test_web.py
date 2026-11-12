"""Talk-or-type options, prompts per mode, and the local web server."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import web_server
from clinic.options import job_options
from clinic.prompts import build_instructions, greeting_instructions

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))


# ---- options from job metadata ---------------------------------------------------

@pytest.mark.parametrize("metadata, expected", [
    ('{"mode": "text", "language": "ru"}', ("text", "ru", "ru")),
    ('{"mode": "voice", "language": "zh"}', ("voice", "zh", "zh")),
    ('{"mode": "text"}', ("text", "ar", None)),
    ('{"mode": "video", "language": "fr"}', ("voice", "ar", None)),   # unknown values
    ('{"language": ["en"]}', ("voice", "ar", None)),                  # wrong type
    ("", ("voice", "ar", None)),                                      # Agent Console session
    (None, ("voice", "ar", None)),
    ("not json", ("voice", "ar", None)),
    ('["text"]', ("voice", "ar", None)),
])
def test_job_options(metadata, expected):
    assert job_options(metadata, "ar") == expected


# ---- prompts ---------------------------------------------------------------------

def test_instructions_differ_by_mode_and_share_the_rules():
    voice = build_instructions(NOW, "Test Clinic", "Asia/Dubai", "ar", "voice")
    text = build_instructions(NOW, "Test Clinic", "Asia/Dubai", "ar", "text")
    assert "spoken phone call" in voice and "text chat" not in voice
    assert "text chat" in text and "spoken phone call" not in text
    for prompt in (voice, text):
        assert "Tuesday, 2026-11-10" in prompt
        assert "starts in Arabic" in prompt
        assert "Never give medical advice" in prompt
        assert "{" not in prompt                      # every placeholder was filled


def test_greeting_uses_chosen_language_and_skips_recording_notice_in_text():
    assert "Hindi only" in greeting_instructions("hi", "voice")
    assert "recorded" in greeting_instructions("hi", "voice")
    assert "recorded" not in greeting_instructions("hi", "text")
    assert "Arabic and one in English" in greeting_instructions(None, "voice")
    assert "Arabic and one in English" in greeting_instructions("fr", "voice")


# ---- web server ------------------------------------------------------------------

def test_clean_choice_keeps_only_known_values():
    assert web_server.clean_choice({"mode": "text", "language": "ar"}) == {"mode": "text", "language": "ar"}
    assert web_server.clean_choice({"mode": "x", "language": "fr", "extra": 1}) == {"mode": "voice", "language": None}
    assert web_server.clean_choice({}) == {"mode": "voice", "language": None}


@pytest.fixture
def base_url():
    httpd = web_server.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def post(url, data: bytes):
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_page_is_served(base_url):
    with urllib.request.urlopen(base_url + "/") as reply:
        page = reply.read().decode()
    assert reply.status == 200
    assert 'id="talk"' in page and 'id="type"' in page


def test_token_endpoint_returns_token_and_choice(base_url, monkeypatch):
    seen = {}

    def fake_create_token(choice):
        seen.update(choice)
        return {"url": "wss://example.invalid", "token": "t", **choice}

    monkeypatch.setattr(web_server, "create_token", fake_create_token)
    status, body = post(base_url + "/token", b'{"mode": "text", "language": "zh", "junk": true}')
    assert status == 200
    assert body == {"url": "wss://example.invalid", "token": "t", "mode": "text", "language": "zh"}
    assert seen == {"mode": "text", "language": "zh"}


def test_token_endpoint_reports_missing_settings(base_url, monkeypatch):
    def missing(choice):
        raise KeyError("LIVEKIT_API_KEY")

    monkeypatch.setattr(web_server, "create_token", missing)
    status, body = post(base_url + "/token", b"{}")
    assert status == 500 and "LIVEKIT_API_KEY is not set" in body["error"]


def test_token_endpoint_rejects_bad_requests(base_url):
    assert post(base_url + "/token", b"not json")[0] == 400
    assert post(base_url + "/token", b"[1, 2]")[0] == 400
    assert post(base_url + "/other", b"{}")[0] == 404
