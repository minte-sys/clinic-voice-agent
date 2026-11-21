"""The actions the agent can take, independent of any voice framework.

Every method returns a JSON-serialisable dict for the LLM:
    {"ok": True, ...}                       on success
    {"ok": False, "error": code, "message"} on failure
and writes one audit record. Validation lives here, not in the prompt.

One ClinicService is created per call. It remembers the caller's language
(`set_language`) so every date and time in a tool result arrives as a phrase
ready to speak in that language.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .speech import (DEFAULT_LANGUAGE, LANGUAGES, normalize_phone, only_digits,
                     spoken_digits, spoken_slot)
from .store import Appointment, ClinicStore, Doctor, StoreError

PARTS_OF_DAY = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}
SEARCH_AHEAD_DAYS = 14
MIN_PHONE_DIGITS = 9


def valid_phone(number: str) -> bool:
    """`number` as returned by normalize_phone.

    UAE numbers must have the right length: a local number (05X XXX XXXX) is
    10 digits and becomes 971 plus 9. Other countries: 9 to 15 digits.
    """
    if number.startswith("0"):              # local number of the wrong length
        return False
    if number.startswith("971"):
        return len(number) == 12
    return MIN_PHONE_DIGITS <= len(number) <= 15


class ClinicService:
    def __init__(
        self,
        store: ClinicStore,
        clock: Callable[[], datetime] | None = None,
        audit: Callable[[dict[str, Any]], None] | None = None,
        clinic: dict[str, Any] | None = None,
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        self.store = store
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE
        self._clock = clock or (lambda: datetime.now(store.tz))
        self._audit = audit or (lambda record: None)
        self.clinic = clinic or {
            "name": "Al Noor Family Clinic (demo)",
            "address": "Demo address, replace with the clinic's",
            "hours": "Sunday to Thursday, 8 in the morning to 8 in the evening",
            "insurance": ["Demo Insurance A", "Demo Insurance B"],
        }

    def now(self) -> datetime:
        return self._clock().astimezone(self.store.tz)

    # ---- tools -------------------------------------------------------------

    def set_language(self, language: str) -> dict:
        args = {"language": language}
        code = (language or "").strip().lower()[:2]
        if code not in LANGUAGES:
            return self._fail("set_language", args, "unsupported_language",
                              f"Supported: {', '.join(LANGUAGES)}. Continue in English or transfer.")
        self.language = code
        return self._done("set_language", args,
                          {"ok": True, "language": code, "language_name": LANGUAGES[code]})

    def clinic_info(self) -> dict:
        now = self.now()
        return self._done("clinic_info", {}, {
            "ok": True,
            **self.clinic,
            "specialties": self.store.specialties(),
            "today": now.date().isoformat(),
            "today_weekday": now.strftime("%A"),
            "time_zone": str(self.store.tz),
        })

    def list_doctors(self, specialty: str | None = None) -> dict:
        args = {"specialty": specialty}
        doctors = self.store.doctors(specialty)
        if specialty and not doctors:
            return self._fail("list_doctors", args, "unknown_specialty",
                              f"No such specialty. Available: {', '.join(self.store.specialties())}")
        return self._done("list_doctors", args,
                          {"ok": True, "doctors": [self._doctor(d) for d in doctors]})

    def find_slots(
        self,
        day: str,
        doctor_id: str | None = None,
        specialty: str | None = None,
        part_of_day: str | None = None,
        limit: int = 3,
    ) -> dict:
        args = {"day": day, "doctor_id": doctor_id, "specialty": specialty,
                "part_of_day": part_of_day, "limit": limit}
        try:
            wanted = date.fromisoformat(day)
        except (TypeError, ValueError):
            return self._fail("find_slots", args, "invalid_date", "Use the form YYYY-MM-DD.")
        if part_of_day and part_of_day not in PARTS_OF_DAY:
            return self._fail("find_slots", args, "invalid_part_of_day",
                              "Use morning, afternoon or evening.")
        now = self.now()
        if wanted < now.date():
            return self._fail("find_slots", args, "date_in_past", "That day has passed.")
        if not (doctor_id or specialty):
            return self._fail("find_slots", args, "need_doctor_or_specialty",
                              "Give a doctor_id or a specialty.")
        try:
            doctors = [self.store.doctor(doctor_id)] if doctor_id else self.store.doctors(specialty)
        except StoreError as exc:
            return self._fail("find_slots", args, exc.code, str(exc))
        if not doctors:
            return self._fail("find_slots", args, "unknown_specialty",
                              f"No such specialty. Available: {', '.join(self.store.specialties())}")
        limit = max(1, min(int(limit), 5))

        slots = self._slots_on(doctors, wanted, part_of_day, now)
        result: dict[str, Any] = {"ok": True, "day": wanted.isoformat(),
                                  "slots": [self._slot(d, s) for d, s in slots[:limit]]}
        if not slots:
            # Offer the nearest day that has something, so the agent can suggest it.
            for offset in range(1, SEARCH_AHEAD_DAYS + 1):
                later = self._slots_on(doctors, wanted + timedelta(days=offset), part_of_day, now)
                if later:
                    result["next_available"] = [self._slot(d, s) for d, s in later[:limit]]
                    break
        return self._done("find_slots", args, result)

    def check_booking(self, doctor_id: str, start: str, patient_name: str, phone: str) -> dict:
        """Validate a booking before the read-back, without booking anything.

        Catches a slot taken since it was offered, a bad phone number or a missing
        name before the caller is asked to confirm, and returns the details as
        phrases ready to read back.
        """
        args = {"doctor_id": doctor_id, "start": start, "patient_name": patient_name, "phone": phone}
        when = self._parse_start(start)
        if when is None:
            return self._fail("check_booking", args, "invalid_time",
                              "Use a start value returned by find_slots.")
        try:
            doctor = self.store.doctor(doctor_id)
        except StoreError as exc:
            return self._fail("check_booking", args, exc.code, str(exc))
        number = normalize_phone(phone)
        if not valid_phone(number):
            return self._fail("check_booking", args, "invalid_phone",
                              "That phone number has the wrong number of digits. UAE mobiles have "
                              "10 digits, like 050 123 4567. Ask the caller to repeat or type it.")
        if len((patient_name or "").strip()) < 2:
            return self._fail("check_booking", args, "invalid_name", "Ask for the patient's name.")
        when = when.astimezone(self.store.tz)
        if when not in self.store.open_slots(doctor.id, when.date(), self.now()):
            return self._fail("check_booking", args, "slot_unavailable",
                              "That slot is no longer free. Call find_slots again and offer others.")
        return self._done("check_booking", args, {
            "ok": True,
            "doctor": self._name(doctor),
            "start": when.isoformat(),
            "spoken": spoken_slot(when, self.language),
            "patient_name": patient_name.strip(),
            "phone_spoken": spoken_digits(number),
        })

    def book_appointment(
        self, doctor_id: str, start: str, patient_name: str, phone: str, idempotency_key: str
    ) -> dict:
        args = {"doctor_id": doctor_id, "start": start, "patient_name": patient_name,
                "phone": phone, "idempotency_key": idempotency_key}
        when = self._parse_start(start)
        if when is None:
            return self._fail("book_appointment", args, "invalid_time",
                              "Use a start value returned by find_slots.")
        number = normalize_phone(phone)
        if not valid_phone(number):
            return self._fail("book_appointment", args, "invalid_phone",
                              "That phone number has the wrong number of digits. UAE mobiles have "
                              "10 digits, like 050 123 4567. Ask the caller to repeat or type it.")
        if len((patient_name or "").strip()) < 2:
            return self._fail("book_appointment", args, "invalid_name", "Ask for the patient's name.")
        try:
            appointment = self.store.book(doctor_id, when, patient_name, number,
                                          idempotency_key, self.now())
        except StoreError as exc:
            return self._fail("book_appointment", args, exc.code, str(exc))
        return self._done("book_appointment", args,
                          {"ok": True, "appointment": self._appointment(appointment)})

    def find_appointments(self, phone: str) -> dict:
        args = {"phone": phone}
        found = self.store.upcoming_for_phone(normalize_phone(phone), self.now())
        return self._done("find_appointments", args,
                          {"ok": True, "appointments": [self._appointment(a) for a in found]})

    def reschedule_appointment(self, code: str, phone: str, new_start: str) -> dict:
        args = {"code": code, "phone": phone, "new_start": new_start}
        when = self._parse_start(new_start)
        if when is None:
            return self._fail("reschedule_appointment", args, "invalid_time",
                              "Use a start value returned by find_slots.")
        try:
            owned = self._owned(code, phone)
            appointment = self.store.reschedule(owned.code, when, self.now())
        except StoreError as exc:
            return self._fail("reschedule_appointment", args, exc.code, str(exc))
        return self._done("reschedule_appointment", args,
                          {"ok": True, "appointment": self._appointment(appointment)})

    def cancel_appointment(self, code: str, phone: str) -> dict:
        args = {"code": code, "phone": phone}
        try:
            owned = self._owned(code, phone)
            appointment = self.store.cancel(owned.code)
        except StoreError as exc:
            return self._fail("cancel_appointment", args, exc.code, str(exc))
        return self._done("cancel_appointment", args,
                          {"ok": True, "appointment": self._appointment(appointment)})

    # ---- helpers -----------------------------------------------------------

    def _owned(self, code: str, phone: str) -> Appointment:
        """A booking is only touched when the caller's phone matches it.

        A wrong code and a wrong phone give the same error, so the tool cannot
        be used to discover which confirmation codes exist.
        """
        try:
            appointment = self.store.by_code(only_digits(code))
        except StoreError:
            appointment = None
        if (appointment is None or appointment.status != "booked"
                or appointment.phone != normalize_phone(phone)):
            error = StoreError("No active booking matches that code and phone number.")
            error.code = "no_match"
            raise error
        return appointment

    def _slots_on(self, doctors: list[Doctor], day: date, part_of_day: str | None,
                  now: datetime) -> list[tuple[Doctor, datetime]]:
        lo, hi = PARTS_OF_DAY.get(part_of_day or "", (0, 24))
        pairs = [(d, s) for d in doctors for s in self.store.open_slots(d.id, day, now)
                 if lo <= s.hour < hi]
        return sorted(pairs, key=lambda pair: (pair[1], pair[0].id))

    def _parse_start(self, value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:  # the LLM dropped the offset: read it as clinic time
            parsed = parsed.replace(tzinfo=self.store.tz)
        return parsed

    def _name(self, d: Doctor) -> str:
        # Names are stored in Arabic and Latin script; other languages use the Latin form.
        return d.name_ar if self.language == "ar" else d.name_en

    def _doctor(self, d: Doctor) -> dict:
        return {"doctor_id": d.id, "name": self._name(d), "specialty": d.specialty,
                "languages": list(d.languages)}

    def _slot(self, d: Doctor, start: datetime) -> dict:
        return {"doctor_id": d.id, "doctor": self._name(d), "start": start.isoformat(),
                "spoken": spoken_slot(start, self.language)}

    def _appointment(self, a: Appointment) -> dict:
        d = self.store.doctor(a.doctor_id)
        return {"code": a.code, "code_spoken": spoken_digits(a.code), "status": a.status,
                "doctor_id": d.id, "doctor": self._name(d), "start": a.start.isoformat(),
                "spoken": spoken_slot(a.start, self.language), "patient_name": a.patient_name}

    def _done(self, tool: str, args: dict, result: dict) -> dict:
        self._audit({"at": self.now().isoformat(), "tool": tool, "args": args,
                     "ok": result.get("ok", False), "error": result.get("error")})
        return result

    def _fail(self, tool: str, args: dict, error: str, message: str) -> dict:
        return self._done(tool, args, {"ok": False, "error": error, "message": message})


def jsonl_audit(path: str) -> Callable[[dict[str, Any]], None]:
    """Audit sink that appends one JSON line per tool call.

    Arguments include names and phone numbers: store this file with the same
    protection as patient records.
    """
    def write(record: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return write
