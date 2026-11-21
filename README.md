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
| Protected confirmation code, interruption threshold | Written against the LiveKit docs, **not yet run** |
| Web server limits, widget, sample site | Tested: served and screenshotted in a browser; token creation stubbed |
| Hosting on LiveKit Cloud and Render, call time limit | Written against the docs, **not yet deployed** |
| Native voice per language (`inworld/inworld-tts-2`) | Written against the LiveKit and Inworld docs, **not yet run** |
| Spoken date and time phrasing | Unit tested for exact output; **not reviewed by native speakers** |

## Layout

```
agent.py            LiveKit agent: voice session or text session, tools
web_server.py       Serves the pages and creates room tokens, with rate limits
web/index.html      Talk or Type page (also shown inside the widget)
web/widget.js       One-line embed: "Book an appointment" button for any website
web/demo-site.html  Sample clinic homepage with the widget, for demos
render.yaml         Hosting for the web server on Render
.dockerignore       Keeps secrets and local data out of the agent image
clinic/store.py     SQLite calendar: one booking per slot, idempotent booking
clinic/tools.py     The agent's actions: validation, phone check, audit log
clinic/speech.py    Dates, times, digits as speakable phrases in 5 languages
clinic/prompts.py   Instructions for voice and for text chat
clinic/voices.py    A native voice per language, and reply-language detection
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

## Put it online (public demo)

Two parts run in two places: the **agent** on LiveKit Cloud, and the **web
server** (pages, widget, tokens) on Render. Both have free tiers.

### 1. Deploy the agent to LiveKit Cloud

From this folder, with the virtual environment active:

```
lk agent create
```

Pick the region closest to your callers when asked. The CLI writes a
`Dockerfile` and `livekit.toml`, uploads the code, builds it and starts it.
LiveKit Cloud supplies the LiveKit keys itself; `.env.local` is never uploaded.

- Stop any local `python agent.py dev` while testing the hosted agent, or the
  two will share the calls.
- After changing the code: `lk agent deploy`.
- Commit `Dockerfile` and `livekit.toml` to GitHub; they contain no secrets.

### 2. Deploy the web server to Render

1. Push this project to GitHub.
2. On render.com: **New**, then **Blueprint**, and pick the repository.
   Render reads `render.yaml`.
3. Enter `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` from the
   same LiveKit project, then deploy.
4. Render gives you an address such as `https://clinic-assistant-web.onrender.com`:
   - `/` is the assistant page
   - `/demo-site.html` is the sample clinic website with the widget

On the free plan the server sleeps after 15 minutes without visitors, so the
first visit after a pause takes about a minute.

### 3. Add it to any website

Paste one line before `</body>` (in WordPress: a Custom HTML block, or the
footer scripts setting of the theme):

```html
<script src="https://YOUR-RENDER-ADDRESS/widget.js" data-language="ar" defer></script>
```

Options: `data-label`, `data-language`, `data-position="left"`, `data-color`.
For a real company's site, set `FRAME_ANCESTORS` on Render to their address so
only their site can embed it.

### Cost protection

Each visitor can start 5 conversations per 10 minutes, everyone together 60 per
hour, and each conversation ends after 8 minutes (`MAX_CALL_MINUTES`). Raise
these for a pilot, not for an open demo.

## Settings

All in `.env.local` (see `.env.example`). The ones you are most likely to change:

- `STT_MODEL`, `STT_LANGUAGE`: speech recognition. Empty language means automatic detection.
- `TTS_MODEL`, `TTS_VOICE_AR` and the other `TTS_VOICE_*`: one native voice per language.
  The agent switches voice whenever its reply changes language.
- `INTERRUPT_MIN_DURATION`: how long the caller must speak before the agent stops talking.
  The confirmation code can't be interrupted; the read-back can, so callers can correct it.
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
6. For a real clinic: the clinic's calendar, legal review of health data, and
   in-country hosting (see the plan).

Demo doctors and clinic details are fictional. Do not take real patient calls
with this prototype: see the data and compliance section of the plan.
