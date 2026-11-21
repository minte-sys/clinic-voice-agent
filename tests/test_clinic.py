"""Stage 0 gate: booking logic, tool validation and spoken formatting."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clinic.speech import normalize_phone, spoken_digits, spoken_slot
from clinic.store import ClinicStore, InvalidSlot, SlotUnavailable, seed_demo
from clinic.tools import ClinicService

TZ = ZoneInfo("Asia/Dubai")
# Tuesday 10 November 2026, 09:00 clinic time.
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
WED_10 = "2026-11-11T10:00:00+04:00"   # a working day for every demo doctor
FRIDAY = "2026-11-13"                   # nobody works


@pytest.fixture
def audit():
    return []


@pytest.fixture
def service(audit):
    store = ClinicStore()
    seed_demo(store)
    return ClinicService(store, clock=lambda: NOW, audit=audit.append)


def book(service, key="k1", start=WED_10, doctor="dr-haddad", phone="050 123 4567"):
    return service.book_appointment(doctor, start, "Sara Ali", phone, key)


# ---- store ---------------------------------------------------------------------

def test_open_slots_skip_past_times_and_days_off(service):
    today = service.store.open_slots("dr-haddad", NOW.date(), NOW)
    assert today[0] == NOW.replace(hour=9, minute=30)      # 09:00 is not after "now"
    assert service.store.open_slots("dr-haddad", datetime(2026, 11, 13).date(), NOW) == []


def test_double_booking_is_rejected_by_the_database(service):
    assert book(service, "k1")["ok"]
    second = book(service, "k2", phone="0509999999")
    assert second == {"ok": False, "error": "slot_unavailable", "message": "that slot was just taken"}


def test_retry_with_same_idempotency_key_returns_same_booking(service):
    first, again = book(service, "k1"), book(service, "k1")
    assert first["appointment"]["code"] == again["appointment"]["code"]
    assert len(service.find_appointments("0501234567")["appointments"]) == 1


@pytest.mark.parametrize("start", [
    "2026-11-10T08:30:00+04:00",   # in the past
    "2026-11-13T10:00:00+04:00",   # Friday, day off
    "2026-11-11T18:00:00+04:00",   # after working hours
    "2026-11-11T10:10:00+04:00",   # off the 30-minute grid
])
def test_invalid_slots_are_rejected(service, start):
    result = book(service, start=start)
    assert result["ok"] is False and result["error"] == "invalid_slot"


def test_store_requires_time_zone(service):
    with pytest.raises(InvalidSlot):
        service.store.book("dr-haddad", datetime(2026, 11, 11, 10), "A B", "971", "k", NOW)


def test_cancelled_slot_can_be_booked_again(service):
    code = book(service, "k1")["appointment"]["code"]
    assert service.cancel_appointment(code, "0501234567")["appointment"]["status"] == "cancelled"
    assert book(service, "k2")["ok"]


def test_reschedule_moves_booking_and_frees_old_slot(service):
    code = book(service, "k1")["appointment"]["code"]
    moved = service.reschedule_appointment(code, "0501234567", "2026-11-11T11:30:00+04:00")
    assert moved["appointment"]["start"] == "2026-11-11T11:30:00+04:00"
    assert book(service, "k2", phone="0507777777")["ok"]      # 10:00 is free again


def test_reschedule_onto_taken_slot_fails(service):
    code = book(service, "k1")["appointment"]["code"]
    book(service, "k2", start="2026-11-11T11:00:00+04:00", phone="0507777777")
    result = service.reschedule_appointment(code, "0501234567", "2026-11-11T11:00:00+04:00")
    assert result["error"] == "slot_unavailable"
    with pytest.raises(SlotUnavailable):
        service.store.reschedule(code, datetime(2026, 11, 11, 11, tzinfo=TZ), NOW)


# ---- tools ---------------------------------------------------------------------

def test_find_slots_limits_and_filters_part_of_day(service):
    result = service.find_slots("2026-11-11", specialty="dermatology", part_of_day="afternoon")
    assert [s["start"][11:16] for s in result["slots"]] == ["12:00", "12:30", "13:00"]


def test_find_slots_suggests_next_available_day(service):
    result = service.find_slots(FRIDAY, doctor_id="dr-haddad")
    assert result["slots"] == []
    assert result["next_available"][0]["start"].startswith("2026-11-15")   # Sunday


def test_find_slots_input_errors(service):
    assert service.find_slots("tomorrow", specialty="dentistry")["error"] == "invalid_date"
    assert service.find_slots("2026-11-09", specialty="dentistry")["error"] == "date_in_past"
    assert service.find_slots("2026-11-11")["error"] == "need_doctor_or_specialty"
    assert service.find_slots("2026-11-11", specialty="astrology")["error"] == "unknown_specialty"
    assert service.find_slots("2026-11-11", doctor_id="dr-nobody")["error"] == "unknown_doctor"


def test_booking_validates_phone_and_name(service):
    assert book(service, phone="12345")["error"] == "invalid_phone"
    assert service.book_appointment("dr-haddad", WED_10, " ", "0501234567", "k")["error"] == "invalid_name"


def test_start_without_offset_is_read_as_clinic_time(service):
    result = book(service, start="2026-11-11T10:00")
    assert result["appointment"]["start"] == WED_10


def test_other_callers_cannot_change_or_probe_a_booking(service):
    code = book(service, "k1")["appointment"]["code"]
    wrong_phone = service.cancel_appointment(code, "0500000000")
    wrong_code = service.cancel_appointment("000000", "0501234567")
    assert wrong_phone["error"] == wrong_code["error"] == "no_match"
    assert wrong_phone["message"] == wrong_code["message"]
    assert service.find_appointments("0500000000")["appointments"] == []


def test_code_spoken_with_spaces_or_arabic_digits_still_matches(service):
    code = book(service, "k1")["appointment"]["code"]
    arabic = code.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    assert service.cancel_appointment(" ".join(arabic), "+971 50 123 4567")["ok"]


def test_every_tool_call_is_audited(service, audit):
    service.clinic_info()
    book(service, "k1")
    book(service, "k2")
    assert [(r["tool"], r["ok"]) for r in audit] == [
        ("clinic_info", True), ("book_appointment", True), ("book_appointment", False)]
    assert audit[-1]["error"] == "slot_unavailable"


# ---- languages -----------------------------------------------------------------

SLOT = datetime(2026, 11, 15, 10, 30, tzinfo=TZ)        # Sunday morning
LATE = datetime(2026, 11, 16, 14, 40, tzinfo=TZ)        # Monday afternoon


@pytest.mark.parametrize("lang, expected", [
    ("en", "Sunday, November 15 at 10:30 in the morning"),
    ("ar", "يوم الأحد 15 نوفمبر الساعة عشر ونص الصبح"),
    ("hi", "रविवार, 15 नवंबर, सुबह साढ़े 10 बजे"),
    ("zh", "11月15日星期日上午10点半"),
    ("ru", "в воскресенье, 15 ноября, в 10:30"),
])
def test_spoken_slot_per_language(lang, expected):
    assert spoken_slot(SLOT, lang) == expected


@pytest.mark.parametrize("lang, expected", [
    ("en", "Monday, November 16 at 2:40 in the afternoon"),
    ("ar", "يوم الاثنين 16 نوفمبر الساعة ثلاث إلا ثلث الظهر"),
    ("hi", "सोमवार, 16 नवंबर, दोपहर 2 बजकर 40 मिनट"),
    ("zh", "11月16日星期一下午2点40分"),
    ("ru", "в понедельник, 16 ноября, в 14:40"),
])
def test_spoken_slot_afternoon(lang, expected):
    assert spoken_slot(LATE, lang) == expected


def test_unknown_language_falls_back_to_english():
    assert spoken_slot(SLOT, "fr") == spoken_slot(SLOT, "en")


def test_set_language_changes_tool_output(service):
    assert service.find_slots("2026-11-11", doctor_id="dr-haddad")["slots"][0]["spoken"].startswith("Wednesday")
    assert service.set_language("ru")["language_name"] == "Russian"
    slot = service.find_slots("2026-11-11", doctor_id="dr-haddad")["slots"][0]
    assert slot["spoken"] == "в среду, 11 ноября, в 9:00"      # her first slot of the day
    assert slot["doctor"] == "Dr. Layla Haddad"
    service.set_language("ar")
    assert service.list_doctors("dermatology")["doctors"][0]["name"] == "الدكتورة ليلى حداد"


def test_unsupported_language_is_refused_and_keeps_current(service):
    service.set_language("hi")
    assert service.set_language("fr")["error"] == "unsupported_language"
    assert service.language == "hi"


def test_doctor_languages_are_exposed(service):
    doctors = service.list_doctors("general practice")["doctors"]
    assert {"zh", "en"} in [set(d["languages"]) for d in doctors]


def test_phone_and_digit_helpers():
    assert normalize_phone("+971 50 123 4567") == "971501234567"
    assert normalize_phone("00971501234567") == "971501234567"
    assert normalize_phone("٠٥٠ ١٢٣ ٤٥٦٧") == "971501234567"
    assert normalize_phone("०५०१२३४५६७") == "971501234567"
    assert spoken_digits("482913") == "4 8 2 9 1 3"


def test_now_plus_small_delta_is_bookable_only_on_grid(service):
    soon = (NOW + timedelta(minutes=30)).isoformat()
    assert book(service, start=soon)["ok"]


# ---- check_booking (validation before the read-back) -------------------------------

def test_check_booking_returns_read_back_without_booking(service):
    result = service.check_booking("dr-haddad", WED_10, "Sara Ali", "050 123 4567")
    assert result["ok"] and result["spoken"] == "Wednesday, November 11 at 10 in the morning"
    assert result["phone_spoken"] == "9 7 1 5 0 1 2 3 4 5 6 7"
    assert result["doctor"] == "Dr. Layla Haddad"
    assert service.find_appointments("0501234567")["appointments"] == []    # nothing booked


def test_check_booking_follows_caller_language(service):
    service.set_language("ar")
    result = service.check_booking("dr-haddad", WED_10, "سارة علي", "0501234567")
    assert result["doctor"] == "الدكتورة ليلى حداد"
    assert result["spoken"].startswith("يوم الأربعاء")


@pytest.mark.parametrize("args, error", [
    (("dr-haddad", "soon", "Sara Ali", "0501234567"), "invalid_time"),
    (("dr-nobody", WED_10, "Sara Ali", "0501234567"), "unknown_doctor"),
    (("dr-haddad", WED_10, "Sara Ali", "12345"), "invalid_phone"),
    (("dr-haddad", WED_10, " ", "0501234567"), "invalid_name"),
    (("dr-haddad", "2026-11-13T10:00:00+04:00", "Sara Ali", "0501234567"), "slot_unavailable"),
])
def test_check_booking_rejects_bad_details(service, args, error):
    assert service.check_booking(*args)["error"] == error


def test_check_booking_catches_slot_taken_since_offer(service):
    book(service, "k1", phone="0507777777")
    assert service.check_booking("dr-haddad", WED_10, "Sara Ali", "0501234567")["error"] == "slot_unavailable"


# ---- phone number lengths (from a test call: a 12-digit "05..." number was accepted) ---

@pytest.mark.parametrize("raw, ok", [
    ("050 123 4567", True),           # UAE local mobile, 10 digits
    ("+971 50 123 4567", True),
    ("00971501234567", True),
    ("056632150934", False),          # 12 digits starting with 0
    ("05012345", False),              # too short
    ("+97150123456789", False),       # UAE with extra digits
    ("+44 20 7946 0958", True),       # other country
    ("12345", False),
])
def test_phone_length_rules(service, raw, ok):
    result = service.check_booking("dr-haddad", WED_10, "Sara Ali", raw)
    assert result["ok"] is ok
    if not ok:
        assert result["error"] == "invalid_phone" and "10 digits" in result["message"]


def test_booking_refuses_wrong_length_phone(service):
    assert book(service, phone="056632150934")["error"] == "invalid_phone"
