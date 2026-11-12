"""Turn structured values into words a text-to-speech voice says correctly.

TTS engines read "2026-11-15T10:30" badly and read long digit strings as one
huge number. The agent is told to speak the `spoken` strings from tool
results instead of formatting dates, times, phone numbers or codes itself.

Supported languages: English (en), Arabic (ar), Hindi (hi), Mandarin Chinese
(zh), Russian (ru). To add one, add a formatter to `_FORMATTERS`.

The Arabic phrasing targets everyday Gulf usage. Have a native speaker of
each language review the output before real callers hear it.
"""
from __future__ import annotations

from datetime import datetime

LANGUAGES = {
    "en": "English",
    "ar": "Arabic",
    "hi": "Hindi",
    "zh": "Mandarin Chinese",
    "ru": "Russian",
}
DEFAULT_LANGUAGE = "en"


def _hour12(hour24: int) -> int:
    return hour24 % 12 or 12


# ---- English ----------------------------------------------------------------

EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]


def _en(dt: datetime) -> str:
    if dt.hour < 12:
        period = "in the morning"
    elif dt.hour < 17:
        period = "in the afternoon"
    else:
        period = "in the evening"
    clock = f"{_hour12(dt.hour)}" if dt.minute == 0 else f"{_hour12(dt.hour)}:{dt.minute:02d}"
    return f"{EN_DAYS[dt.weekday()]}, {EN_MONTHS[dt.month - 1]} {dt.day} at {clock} {period}"


# ---- Arabic (Gulf) ------------------------------------------------------------

AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
             "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
AR_HOURS = {1: "وحدة", 2: "ثنتين", 3: "ثلاث", 4: "أربع", 5: "خمس", 6: "ست",
            7: "سبع", 8: "ثمان", 9: "تسع", 10: "عشر", 11: "إحدعش", 12: "اثنعش"}


def _ar_period(hour24: int) -> str:
    if hour24 < 12:
        return "الصبح"
    if hour24 < 15:
        return "الظهر"
    if hour24 < 18:
        return "العصر"
    return "المسا"


def _ar_time(dt: datetime) -> str:
    hour, minute = dt.hour, dt.minute
    period = _ar_period(hour)
    this_hour, next_hour = AR_HOURS[_hour12(hour)], AR_HOURS[_hour12(hour + 1)]
    if minute == 0:
        return f"الساعة {this_hour} {period}"
    if minute == 15:
        return f"الساعة {this_hour} وربع {period}"
    if minute == 20:
        return f"الساعة {this_hour} وثلث {period}"
    if minute == 30:
        return f"الساعة {this_hour} ونص {period}"
    if minute == 40:
        return f"الساعة {next_hour} إلا ثلث {period}"
    if minute == 45:
        return f"الساعة {next_hour} إلا ربع {period}"
    return f"الساعة {this_hour} و{minute} دقيقة {period}"


def _ar(dt: datetime) -> str:
    return f"يوم {AR_DAYS[dt.weekday()]} {dt.day} {AR_MONTHS[dt.month - 1]} {_ar_time(dt)}"


# ---- Hindi --------------------------------------------------------------------

HI_DAYS = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
HI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई",
             "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


def _hi(dt: datetime) -> str:
    hour, minute = dt.hour, dt.minute
    if hour < 12:
        period = "सुबह"
    elif hour < 16:
        period = "दोपहर"
    elif hour < 20:
        period = "शाम"
    else:
        period = "रात"
    h = _hour12(hour)
    if minute == 0:
        clock = f"{h} बजे"
    elif minute == 30 and h == 1:
        clock = "डेढ़ बजे"
    elif minute == 30 and h == 2:
        clock = "ढाई बजे"
    elif minute == 30:
        clock = f"साढ़े {h} बजे"
    else:
        clock = f"{h} बजकर {minute} मिनट"
    return f"{HI_DAYS[dt.weekday()]}, {dt.day} {HI_MONTHS[dt.month - 1]}, {period} {clock}"


# ---- Mandarin Chinese ---------------------------------------------------------

ZH_DAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _zh(dt: datetime) -> str:
    hour, minute = dt.hour, dt.minute
    if hour < 12:
        period = "上午"
    elif hour < 13:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    clock = f"{_hour12(hour)}点"
    if minute == 30:
        clock += "半"
    elif minute:
        clock += f"{minute}分"
    return f"{dt.month}月{dt.day}日{ZH_DAYS[dt.weekday()]}{period}{clock}"


# ---- Russian ------------------------------------------------------------------

RU_ON_DAY = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу",
             "в субботу", "в воскресенье"]
RU_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                 "августа", "сентября", "октября", "ноября", "декабря"]


def _ru(dt: datetime) -> str:
    # The 24-hour clock is the everyday form for appointments in Russian.
    return (f"{RU_ON_DAY[dt.weekday()]}, {dt.day} {RU_MONTHS_GEN[dt.month - 1]}, "
            f"в {dt.hour}:{dt.minute:02d}")


_FORMATTERS = {"en": _en, "ar": _ar, "hi": _hi, "zh": _zh, "ru": _ru}


def spoken_slot(dt: datetime, lang: str) -> str:
    """A date and time as a phrase a voice can read aloud in `lang`.

    en: 'Sunday, November 15 at 10:30 in the morning'. Unknown languages fall
    back to English.
    """
    return _FORMATTERS.get(lang, _en)(dt)


# ---- digits and phone numbers -------------------------------------------------

# Arabic-Indic, Persian and Devanagari digits -> ASCII.
_TO_ASCII_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩" "۰۱۲۳۴۵۶۷۸۹" "०१२३४५६७८९",
    "0123456789" * 3,
)


def only_digits(raw: str) -> str:
    return "".join(ch for ch in raw.translate(_TO_ASCII_DIGITS) if ch in "0123456789")


def spoken_digits(value: str) -> str:
    """'482913' -> '4 8 2 9 1 3', so a voice reads digits one at a time."""
    return " ".join(only_digits(value))


def normalize_phone(raw: str, default_country: str = "971") -> str:
    """Digits only, in international form without '+'.

    '+971 50 123 4567', '00971501234567', '050 123 4567' -> '971501234567'.
    The local-number rule (leading 0, 10 digits) assumes UAE mobiles; change
    `default_country` and this rule for other markets.
    """
    digits = only_digits(raw)
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = default_country + digits[1:]
    return digits
