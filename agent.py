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

import os
import uuid

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (Agent, AgentServer, AgentSession, MetricsCollectedEvent,
                            RunContext, TurnHandlingOptions, function_tool, inference,
                            metrics, room_io)

from clinic.options import job_options
from clinic.prompts import build_instructions, greeting_instructions
from clinic.speech import LANGUAGES
from clinic.store import ClinicStore, seed_demo
from clinic.tools import ClinicService, jsonl_audit

load_dotenv(".env.local")

STT_MODEL = os.getenv("STT_MODEL", "google/gemini-3.5-transcribe-live")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "")          # empty = detect automatically
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4.1-mini")
TTS_MODEL = os.getenv("TTS_MODEL", "fishaudio/s2.1-pro")
TTS_VOICE = os.getenv("TTS_VOICE", "")
START_LANGUAGE = os.getenv("START_LANGUAGE", "ar")
# Longest wait, in seconds, before deciding the caller has finished speaking.
ENDPOINT_MAX_DELAY = float(os.getenv("ENDPOINT_MAX_DELAY", "2.5"))

# One store for the worker process. Swap for the clinic's real calendar system in stage 3.
STORE = ClinicStore(os.getenv("CLINIC_DB", "clinic.db"), tz=os.getenv("CLINIC_TZ", "Asia/Dubai"))
seed_demo(STORE)


class ClinicAgent(Agent):
    """Thin adapter: each tool forwards to ClinicService, which validates and audits."""

    def __init__(self, service: ClinicService, mode: str = "voice") -> None:
        self.service = service
        self.transfer_requested = False
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
        return self.service.set_language(language)

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
        return self.service.book_appointment(doctor_id, start, patient_name, phone, idempotency_key)

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
        return self.service.reschedule_appointment(code, phone, new_start)

    @function_tool()
    async def cancel_appointment(self, context: RunContext, code: str, phone: str) -> dict:
        """Cancel a booking. Call only after the person confirmed the cancellation.

        Args:
            code: The confirmation code.
            phone: The phone number used for the booking.
        """
        return self.service.cancel_appointment(code, phone)

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


def voice_session() -> AgentSession:
    stt_options = {"model": STT_MODEL}
    if STT_LANGUAGE:
        stt_options["language"] = STT_LANGUAGE
    elif STT_MODEL.startswith("google/"):
        # Limit automatic detection to the supported languages.
        stt_options["extra_kwargs"] = {"language_codes": list(LANGUAGES)}

    tts_options = {"model": TTS_MODEL}
    if TTS_VOICE:
        tts_options["voice"] = TTS_VOICE

    return AgentSession(
        stt=inference.STT(**stt_options),
        llm=inference.LLM(model=LLM_MODEL),
        tts=inference.TTS(**tts_options),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={"min_delay": 0.3, "max_delay": ENDPOINT_MAX_DELAY},
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
        session = voice_session()
        room_options = room_io.RoomOptions()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)

    await session.start(room=ctx.room, agent=agent, room_options=room_options)
    await session.generate_reply(instructions=greeting_instructions(chosen_language, mode))


if __name__ == "__main__":
    agents.cli.run_app(server)
