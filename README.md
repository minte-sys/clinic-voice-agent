# Clinic booking assistant prototype

An assistant that books, reschedules and cancels clinic appointments by voice
or by text chat, in English, Arabic, Hindi, Mandarin Chinese and Russian. The
caller chooses Talk or Type, and a language, on a web page.

## What is verified and what is not

| Part | Status |
| --- | --- |
| `clinic/` booking core, prompts, options | Tested: `python -m pytest` passes |
| `web_server.py` page serving and request handling | Tested, with token creation stubbed |
| Voice calls from the LiveKit Agent Console | Run successfully on LiveKit Agents 1.8.11 in English and Arabic |
| Web page (`web/index.html`), real token creation, text chat mode | Written against the LiveKit docs (November 2026), **not yet run** |
| Hindi, Chinese and Russian speech | **Not yet tested** by a speaker |
| Spoken date and time phrasing | Unit tested for exact output; **not reviewed by native speakers** |

## Layout

```
agent.py            LiveKit agent: voice session or text session, tools
web_server.py       Serves the page and creates room tokens (local use only)
web/index.html      Talk or Type page
clinic/store.py     SQLite calendar: one booking per slot, idempotent booking
clinic/tools.py     The agent's actions: validation, phone check, audit log
clinic/speech.py    Dates, times, digits as speakable phrases in 5 languages
clinic/prompts.py   Instructions for voice and for text chat
clinic/options.py   Reads the page's choice (mode, language)
tests/              Booking logic, tools, languages, prompts, web server
```

## Run it

Use two terminals, both inside this folder with the virtual environment active.

Terminal 1, the agent:

```
python agent.py dev
```

Terminal 2, the web page:

```
python web_server.py
```

Then open http://localhost:8000, pick a language, and choose Talk or Type.

The LiveKit Agent Console still works too; sessions started there are voice calls.

## Settings

All in `.env.local` (see `.env.example`). The ones you are most likely to change:

- `STT_MODEL`, `STT_LANGUAGE`: speech recognition. Empty language means automatic detection.
- `ENDPOINT_MAX_DELAY`: how long the agent waits after short phrases. Try `1.5` for faster replies.
- `START_LANGUAGE`: used when the caller did not pick a language.

## How the modes work

- **Talk:** speech-to-text, LLM, text-to-speech. The page also shows a live
  transcript, and the caller can type at any time, which helps with names and
  phone numbers.
- **Type:** LLM only. No speech models run, so replies are faster and cheaper.

Both modes use the same tools, rules and calendar.

## Known gaps, in build order

1. Speed tuning for voice (measurements are logged as `EOU`, `LLM` and `TTS metrics`).
2. Scripted conversations that check tool calls and final calendar state.
3. Real human handoff: `transfer_to_human` only records the request.
4. Written confirmations by SMS or WhatsApp.
5. A real clinic calendar behind `ClinicStore`'s interface.
6. Login and rate limits on `/token` before the page is hosted anywhere public.

Demo doctors and clinic details are fictional. Do not take real patient calls
with this prototype: see the data and compliance section of the plan.
