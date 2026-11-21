"""Clinic booking agent on LiveKit Agents: voice calls and text chat.

Start the agent:      python agent.py dev
Start the web page:   python web_server.py      (then open http://localhost:8000)

The web page lets the caller choose Talk or Type and a language. That choice
arrives here as job metadata. Sessions started from the LiveKit Agent Console
carry no metadata and run as voice calls.

The booking logic lives in the `clinic` package and is tested without LiveKit.
Model choices are environment variables (.env.local) so providers can be
swapped without code changes.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterable

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (Agent, AgentServer, AgentSession, MetricsCollectedEvent,
                            ModelSettings, RunContext, TurnHandlingOptions, function_tool,
                            inference, metrics, room_io)

from clinic.options import job_options
from clinic.prompts import build_instructions, greeting_instructions
from clinic.speech import LANGUAGES
from clinic.store import ClinicStore, seed_demo
from clinic.tools import ClinicService, jsonl_audit
from clinic.voices import detect_language, voice_for

load_dotenv(".env.local")

STT_MODEL = os.getenv("STT_MODEL", "google/gemini-3.5-transcribe-live")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "")          # empty = detect automatically
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
# One native voice per language (see clinic/voices.py). Override with TTS_VOICE_AR etc.
TTS_MODEL = os.getenv("TTS_MODEL", "inworld/inworld-tts-2")
START_LANGUAGE = os.getenv("START_LANGUAGE", "ar")
# Longest wait, in seconds, before deciding the caller has finished speaking.
ENDPOINT_MAX_DELAY = float(os.getenv("ENDPOINT_MAX_DELAY", "2.5"))
# How long the caller must speak, in seconds, before the agent stops talking. Short
# sounds such as "mm-hmm" or a cough stay below it.
INTERRUPT_MIN_DURATION = float(os.getenv("INTERRUPT_MIN_DURATION", "0.6"))
# Longest conversation, in minutes, before the agent says goodbye. Caps the cost of a
# public demo. 0 turns the limit off.
MAX_CALL_MINUTES = float(os.getenv("MAX_CALL_MINUTES", "8"))


def protect_reply(context: RunContext) -> None:
    """Make the reply that follows this tool call play to the end, even if the caller
    talks over it. Used for the confirmation code after a booking or change."""
    try:
        context.disallow_interruptions()
    except Exception:   # never let this block a booking
        pass

# One store for the worker process. Swap for the clinic's real calendar system in stage 3.
STORE = ClinicStore(os.getenv("CLINIC_DB", "clinic.db"), tz=os.getenv("CLINIC_TZ", "Asia/Dubai"))
seed_demo(STORE)


class ClinicAgent(Agent):
    """Thin adapter: each tool forwards to ClinicService, which validates and audits."""

    def __init__(self, service: ClinicService, mode: str = "voice") -> None:
        self.service = service
        self.transfer_requested = False
        self.voice_language = service.language      # language of the voice now in use
        super().__init__(
            instructions=build_instructions(
                service.now(), service.clinic["name"], str(service.store.tz),
                service.language, mode,
            )
        )

    @function_tool()
    async def set_language(self, context: RunContext, language: str) -> dict:
        """Record the person's main language so dates and times come back in it.

        Call this first whenever the person's language changes.

        Args:
            language: One of "en", "ar", "hi", "zh", "ru".
        """
        result = self.service.set_language(language)
        if result.get("ok"):
            self.use_voice(result["language"])
        return result

    def use_voice(self, language: str) -> None:
        """Switch the speaking voice to the native voice for `language`.

        Takes effect from the next sentence the agent speaks. Does nothing in text
        mode, where there is no voice.
        """
        if language == self.voice_language or language not in LANGUAGES:
            return
        try:
            tts = self.session.tts
        except RuntimeError:          # session not running yet
            return
        if tts is None:
            return
        options = {"language": language}
        voice = voice_for(language, TTS_MODEL, os.environ)
        if voice:
            options["voice"] = voice
        tts.update_options(**options)
        self.voice_language = language

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        """Before speaking a reply, check its language and switch to the native voice.

        This catches replies where the model changed language without calling
        set_language. Speech starts once the first sentence is complete anyway, so
        reading the first few words first adds no noticeable delay.
        """
        chunks = aiter(text)
        head: list[str] = []
        letters = 0
        while letters < 20:
            try:
                chunk = await anext(chunks)
            except StopAsyncIteration:
                break
            head.append(chunk)
            letters += sum(ch.isalpha() for ch in chunk)

        language = detect_language("".join(head))
        if language:
            self.use_voice(language)

        async def whole_reply():
            for chunk in head:
                yield chunk
            async for chunk in chunks:
                yield chunk

        async for frame in Agent.default.tts_node(self, whole_reply(), model_settings):
            yield frame

    @function_tool()
    async def clinic_info(self, context: RunContext) -> dict:
        """Clinic name, address, opening hours, accepted insurance, specialties and today's date."""
        return self.service.clinic_info()

    @function_tool()
    async def list_doctors(self, context: RunContext, specialty: str | None = None) -> dict:
        """List doctors and the languages they speak, optionally for one specialty.

        Args:
            specialty: A specialty from clinic_info, in English, for example "dermatology".
        """
        return self.service.list_doctors(specialty)

    @function_tool()
    async def find_slots(
        self,
        context: RunContext,
        day: str,
        doctor_id: str | None = None,
        specialty: str | None = None,
        part_of_day: str | None = None,
    ) -> dict:
        """Find open appointment slots on one day. The only source of availability.

        Give doctor_id or specialty. If the day is full the result has `next_available`.

        Args:
            day: The day as YYYY-MM-DD.
            doctor_id: A doctor_id from list_doctors.
            specialty: A specialty in English, if the person has no preferred doctor.
            part_of_day: "morning", "afternoon" or "evening".
        """
        return self.service.find_slots(day, doctor_id, specialty, part_of_day)

    @function_tool()
    async def check_booking(
        self, context: RunContext, doctor_id: str, start: str, patient_name: str, phone: str
    ) -> dict:
        """Check the details before reading them back. Books nothing.

        Call after collecting slot, name and phone, then read back the result.

        Args:
            doctor_id: The slot's doctor_id from find_slots.
            start: The slot's exact `start` value from find_slots.
            patient_name: The patient's full name.
            phone: The patient's phone number, digits only.
        """
        # The read-back stays interruptible: callers cut in to correct a name or number.
        return self.service.check_booking(doctor_id, start, patient_name, phone)

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext,
        doctor_id: str,
        start: str,
        patient_name: str,
        phone: str,
        idempotency_key: str,
    ) -> dict:
        """Book a slot. Call only after the person confirmed doctor, day, time and name.

        Args:
            doctor_id: The slot's doctor_id from find_slots.
            start: The slot's exact `start` value from find_slots.
            patient_name: The patient's full name.
            phone: The patient's phone number, digits only.
            idempotency_key: A new random string per booking; reuse it only when retrying.
        """
        result = self.service.book_appointment(doctor_id, start, patient_name, phone, idempotency_key)
        if result.get("ok"):
            protect_reply(context)      # the confirmation code must be heard in full
        return result

    @function_tool()
    async def find_appointments(self, context: RunContext, phone: str) -> dict:
        """List the upcoming appointments booked with this phone number.

        Args:
            phone: The phone number the person gave for their booking.
        """
        return self.service.find_appointments(phone)

    @function_tool()
    async def reschedule_appointment(
        self, context: RunContext, code: str, phone: str, new_start: str
    ) -> dict:
        """Move a booking to another open slot with the same doctor.

        Args:
            code: The confirmation code.
            phone: The phone number used for the booking.
            new_start: The new slot's exact `start` value from find_slots.
        """
        result = self.service.reschedule_appointment(code, phone, new_start)
        if result.get("ok"):
            protect_reply(context)
        return result

    @function_tool()
    async def cancel_appointment(self, context: RunContext, code: str, phone: str) -> dict:
        """Cancel a booking. Call only after the person confirmed the cancellation.

        Args:
            code: The confirmation code.
            phone: The phone number used for the booking.
        """
        result = self.service.cancel_appointment(code, phone)
        if result.get("ok"):
            protect_reply(context)
        return result

    @function_tool()
    async def transfer_to_human(self, context: RunContext, reason: str) -> dict:
        """Hand the conversation to a human receptionist.

        Args:
            reason: One sentence in English on why, for the receptionist.
        """
        # Prototype: record the request. A later stage brings a receptionist into this
        # room with the conversation summary.
        self.transfer_requested = True
        self.service._done("transfer_to_human", {"reason": reason}, {"ok": True})
        return {"ok": True, "message": "Tell the person a receptionist will join shortly."}


def voice_session(language: str) -> AgentSession:
    stt_options = {"model": STT_MODEL}
    if STT_LANGUAGE:
        stt_options["language"] = STT_LANGUAGE
    elif STT_MODEL.startswith("google/"):
        # Limit automatic detection to the supported languages.
        stt_options["extra_kwargs"] = {"language_codes": list(LANGUAGES)}

    tts_options = {"model": TTS_MODEL, "language": language}
    voice = voice_for(language, TTS_MODEL, os.environ)
    if voice:
        tts_options["voice"] = voice

    return AgentSession(
        stt=inference.STT(**stt_options),
        llm=inference.LLM(model=LLM_MODEL),
        tts=inference.TTS(**tts_options),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={"min_delay": 0.3, "max_delay": ENDPOINT_MAX_DELAY},
            interruption={"min_duration": INTERRUPT_MIN_DURATION},
        ),
    )


server = AgentServer()


@server.rtc_session(agent_name="clinic-agent")
async def clinic_agent(ctx: agents.JobContext):
    mode, language, chosen_language = job_options(ctx.job.metadata, START_LANGUAGE)
    call_id = uuid.uuid4().hex[:12]
    audit = jsonl_audit(os.getenv("AUDIT_LOG", "audit.jsonl"))
    service = ClinicService(
        STORE,
        audit=lambda record: audit({"call": call_id, "mode": mode, **record}),
        language=language,
    )
    agent = ClinicAgent(service, mode)

    if mode == "text":
        # Text chat needs no speech models: messages arrive on the lk.chat topic
        # and replies go out on lk.transcription.
        session = AgentSession(llm=inference.LLM(model=LLM_MODEL))
        room_options = room_io.RoomOptions(
            text_input=True, text_output=True, audio_input=False, audio_output=False
        )
    else:
        session = voice_session(language)
        room_options = room_io.RoomOptions()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)

    await session.start(room=ctx.room, agent=agent, room_options=room_options)

    if MAX_CALL_MINUTES > 0:
        async def end_after_limit():
            await asyncio.sleep(MAX_CALL_MINUTES * 60)
            try:
                await session.generate_reply(
                    instructions=("In the person's language and in one short sentence, say this demo "
                                  "conversation has reached its time limit, thank them and say goodbye."),
                    allow_interruptions=False,
                )
            finally:
                ctx.shutdown(reason="time limit reached")

        limit_task = asyncio.create_task(end_after_limit())

        async def cancel_limit():
            limit_task.cancel()

        ctx.add_shutdown_callback(cancel_limit)

    await session.generate_reply(
        instructions=greeting_instructions(language, mode, offer_languages=chosen_language is None)
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
