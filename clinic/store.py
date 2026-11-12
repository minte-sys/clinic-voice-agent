"""SQLite-backed clinic calendar.

Guarantees the agent relies on:
  * a slot can hold at most one active booking (unique index, not app logic)
  * booking is idempotent per idempotency key, so a retried tool call
    returns the first booking instead of creating a second one
  * slots in the past, off the doctor's schedule, or off the slot grid
    are rejected
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    id            TEXT PRIMARY KEY,
    name_en       TEXT NOT NULL,
    name_ar       TEXT NOT NULL,
    specialty     TEXT NOT NULL,
    languages     TEXT NOT NULL,          -- comma separated: "ar,en"
    work_days     TEXT NOT NULL,          -- comma separated weekday(): Mon=0 .. Sun=6
    start_hour    INTEGER NOT NULL,
    end_hour      INTEGER NOT NULL,
    slot_minutes  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT NOT NULL UNIQUE,
    doctor_id        TEXT NOT NULL REFERENCES doctors(id),
    start            TEXT NOT NULL,       -- ISO 8601 with offset, clinic time zone
    patient_name     TEXT NOT NULL,
    phone            TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'booked',   -- booked | cancelled
    idempotency_key  TEXT NOT NULL UNIQUE,
    created_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot
    ON appointments(doctor_id, start) WHERE status = 'booked';
CREATE INDEX IF NOT EXISTS appointments_by_phone ON appointments(phone);
"""


class StoreError(Exception):
    """Base class; `code` is a stable string the agent tools pass to the LLM."""

    code = "error"


class UnknownDoctor(StoreError):
    code = "unknown_doctor"


class InvalidSlot(StoreError):
    code = "invalid_slot"


class SlotUnavailable(StoreError):
    code = "slot_unavailable"


class UnknownAppointment(StoreError):
    code = "unknown_appointment"


@dataclass(frozen=True)
class Doctor:
    id: str
    name_en: str
    name_ar: str
    specialty: str
    languages: tuple[str, ...]
    work_days: frozenset[int]
    start_hour: int
    end_hour: int
    slot_minutes: int


@dataclass(frozen=True)
class Appointment:
    code: str
    doctor_id: str
    start: datetime
    patient_name: str
    phone: str
    status: str


class ClinicStore:
    def __init__(self, path: str = ":memory:", tz: str = "Asia/Dubai") -> None:
        self.tz = ZoneInfo(tz)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.executescript(SCHEMA)

    # ---- doctors -----------------------------------------------------------

    def add_doctor(self, doctor: Doctor) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO doctors VALUES (?,?,?,?,?,?,?,?,?)",
            (
                doctor.id,
                doctor.name_en,
                doctor.name_ar,
                doctor.specialty,
                ",".join(doctor.languages),
                ",".join(str(d) for d in sorted(doctor.work_days)),
                doctor.start_hour,
                doctor.end_hour,
                doctor.slot_minutes,
            ),
        )

    def doctors(self, specialty: str | None = None) -> list[Doctor]:
        if specialty:
            rows = self._db.execute(
                "SELECT * FROM doctors WHERE lower(specialty) = lower(?) ORDER BY id",
                (specialty.strip(),),
            )
        else:
            rows = self._db.execute("SELECT * FROM doctors ORDER BY id")
        return [self._doctor(r) for r in rows]

    def doctor(self, doctor_id: str) -> Doctor:
        row = self._db.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if row is None:
            raise UnknownDoctor(f"no doctor with id {doctor_id!r}")
        return self._doctor(row)

    def specialties(self) -> list[str]:
        rows = self._db.execute("SELECT DISTINCT specialty FROM doctors ORDER BY specialty")
        return [r[0] for r in rows]

    # ---- slots -------------------------------------------------------------

    def open_slots(self, doctor_id: str, day: date, now: datetime) -> list[datetime]:
        """Free slots for one doctor on one day, earliest first, never in the past."""
        doctor = self.doctor(doctor_id)
        if day.weekday() not in doctor.work_days:
            return []
        taken = {
            r[0]
            for r in self._db.execute(
                "SELECT start FROM appointments WHERE doctor_id = ? AND status = 'booked' "
                "AND start LIKE ?",
                (doctor_id, f"{day.isoformat()}%"),
            )
        }
        slots = []
        cursor = datetime.combine(day, time(doctor.start_hour), tzinfo=self.tz)
        end = datetime.combine(day, time(doctor.end_hour), tzinfo=self.tz)
        step = timedelta(minutes=doctor.slot_minutes)
        while cursor < end:
            if cursor > now and cursor.isoformat() not in taken:
                slots.append(cursor)
            cursor += step
        return slots

    def _check_slot(self, doctor: Doctor, start: datetime, now: datetime) -> None:
        if start.tzinfo is None:
            raise InvalidSlot("slot time must carry a time zone")
        start = start.astimezone(self.tz)
        if start <= now:
            raise InvalidSlot("slot is in the past")
        if start.weekday() not in doctor.work_days:
            raise InvalidSlot("doctor does not work that day")
        if not (doctor.start_hour <= start.hour < doctor.end_hour):
            raise InvalidSlot("outside the doctor's working hours")
        minutes = start.hour * 60 + start.minute
        if start.second or start.microsecond or minutes % doctor.slot_minutes:
            raise InvalidSlot("time is not on the slot grid")

    # ---- bookings ----------------------------------------------------------

    def book(
        self,
        doctor_id: str,
        start: datetime,
        patient_name: str,
        phone: str,
        idempotency_key: str,
        now: datetime,
    ) -> Appointment:
        doctor = self.doctor(doctor_id)
        with self._lock:
            existing = self._db.execute(
                "SELECT * FROM appointments WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return self._appointment(existing)
            self._check_slot(doctor, start, now)
            start = start.astimezone(self.tz)
            code = self._new_code()
            try:
                self._db.execute(
                    "INSERT INTO appointments "
                    "(code, doctor_id, start, patient_name, phone, idempotency_key, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        code,
                        doctor_id,
                        start.isoformat(),
                        patient_name.strip(),
                        phone,
                        idempotency_key,
                        now.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SlotUnavailable("that slot was just taken") from exc
            return self.by_code(code)

    def by_code(self, code: str) -> Appointment:
        row = self._db.execute("SELECT * FROM appointments WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise UnknownAppointment(f"no appointment with code {code!r}")
        return self._appointment(row)

    def upcoming_for_phone(self, phone: str, now: datetime) -> list[Appointment]:
        rows = self._db.execute(
            "SELECT * FROM appointments WHERE phone = ? AND status = 'booked' ORDER BY start",
            (phone,),
        )
        return [a for a in map(self._appointment, rows) if a.start > now]

    def cancel(self, code: str) -> Appointment:
        with self._lock:
            self.by_code(code)  # raises if unknown
            self._db.execute("UPDATE appointments SET status = 'cancelled' WHERE code = ?", (code,))
            return self.by_code(code)

    def reschedule(self, code: str, new_start: datetime, now: datetime) -> Appointment:
        with self._lock:
            current = self.by_code(code)
            if current.status != "booked":
                raise UnknownAppointment("appointment is not active")
            doctor = self.doctor(current.doctor_id)
            self._check_slot(doctor, new_start, now)
            new_start = new_start.astimezone(self.tz)
            try:
                self._db.execute(
                    "UPDATE appointments SET start = ? WHERE code = ?",
                    (new_start.isoformat(), code),
                )
            except sqlite3.IntegrityError as exc:
                raise SlotUnavailable("that slot was just taken") from exc
            return self.by_code(code)

    # ---- helpers -----------------------------------------------------------

    def _new_code(self) -> str:
        while True:
            code = f"{secrets.randbelow(1_000_000):06d}"
            if not self._db.execute("SELECT 1 FROM appointments WHERE code = ?", (code,)).fetchone():
                return code

    @staticmethod
    def _doctor(row: sqlite3.Row) -> Doctor:
        return Doctor(
            id=row["id"],
            name_en=row["name_en"],
            name_ar=row["name_ar"],
            specialty=row["specialty"],
            languages=tuple(row["languages"].split(",")),
            work_days=frozenset(int(d) for d in row["work_days"].split(",")),
            start_hour=row["start_hour"],
            end_hour=row["end_hour"],
            slot_minutes=row["slot_minutes"],
        )

    @staticmethod
    def _appointment(row: sqlite3.Row) -> Appointment:
        return Appointment(
            code=row["code"],
            doctor_id=row["doctor_id"],
            start=datetime.fromisoformat(row["start"]),
            patient_name=row["patient_name"],
            phone=row["phone"],
            status=row["status"],
        )


# Gulf working week: Sunday to Thursday. datetime.weekday(): Mon=0 .. Sun=6.
SUN_TO_THU = frozenset({6, 0, 1, 2, 3})
SAT_TO_WED = frozenset({5, 6, 0, 1, 2})


def seed_demo(store: ClinicStore) -> None:
    """Fictional doctors for the prototype. Replace with a real clinic integration."""
    for doctor in (
        Doctor("dr-haddad", "Dr. Layla Haddad", "الدكتورة ليلى حداد", "dermatology",
               ("ar", "en", "ru"), SUN_TO_THU, 9, 17, 30),
        Doctor("dr-rahman", "Dr. Omar Rahman", "الدكتور عمر رحمن", "dentistry",
               ("ar", "en", "hi"), SUN_TO_THU, 10, 19, 30),
        Doctor("dr-menon", "Dr. Priya Menon", "الدكتورة بريا مينون", "pediatrics",
               ("en", "hi"), SAT_TO_WED, 8, 14, 20),
        Doctor("dr-saleh", "Dr. Yousef Saleh", "الدكتور يوسف صالح", "general practice",
               ("ar", "en"), SUN_TO_THU, 8, 20, 20),
        Doctor("dr-zhao", "Dr. Lin Zhao", "الدكتورة لين تشاو", "general practice",
               ("zh", "en"), SUN_TO_THU, 9, 17, 20),
    ):
        store.add_doctor(doctor)
