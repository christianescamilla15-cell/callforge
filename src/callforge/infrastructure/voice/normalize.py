"""Text normalization before TTS. Reading raw LLM output verbatim is the #1
cause of robotic-sounding speech: markdown noise, digits, times and URLs all
need to become speakable Spanish words."""
from __future__ import annotations

import re

_UNITS = [
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
    "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
    "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",
    "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco",
    "veintiséis", "veintisiete", "veintiocho", "veintinueve",
]
_TENS = {
    30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta",
    70: "setenta", 80: "ochenta", 90: "noventa",
}
_HUNDREDS = {
    100: "cien", 200: "doscientos", 300: "trescientos", 400: "cuatrocientos",
    500: "quinientos", 600: "seiscientos", 700: "setecientos",
    800: "ochocientos", 900: "novecientos",
}


def number_to_words(n: int) -> str:
    """Spanish words for 0..999999. Out-of-range returns digits unchanged."""
    if n < 0 or n > 999_999:
        return str(n)
    if n < 30:
        return _UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        base = _TENS[tens * 10]
        return f"{base} y {_UNITS[unit]}" if unit else base
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        if n == 100:
            return "cien"
        base = "ciento" if hundreds == 1 else _HUNDREDS[hundreds * 100]
        return f"{base} {number_to_words(rest)}" if rest else base
    thousands, rest = divmod(n, 1000)
    prefix = "mil" if thousands == 1 else f"{number_to_words(thousands)} mil"
    return f"{prefix} {number_to_words(rest)}" if rest else prefix


_ABBREVIATIONS = {
    "sr.": "señor", "sra.": "señora", "dr.": "doctor", "dra.": "doctora",
    "núm.": "número", "no.": "número", "tel.": "teléfono",
    "min.": "minutos", "seg.": "segundos", "hrs.": "horas", "hr.": "hora",
    "etc.": "etcétera", "ej.": "ejemplo", "aprox.": "aproximadamente",
}

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_MD_NOISE = re.compile(r"[*_`#>|~]+")
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff⬀-⯿️]+"
)
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_PERCENT = re.compile(r"\b(\d{1,6})\s*%")
_NUMBER = re.compile(r"\b\d{1,6}\b")
_SPACES = re.compile(r"[ \t]{2,}")


def _time_to_words(match: re.Match) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return match.group(0)
    hour_words = number_to_words(hour)
    if minute == 0:
        return f"{hour_words} en punto"
    return f"{hour_words} {number_to_words(minute)}"


def normalize_for_tts(text: str) -> str:
    """Make LLM output speakable: strip visual noise, expand digits/times."""
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("el enlace indicado", text)
    text = _MD_NOISE.sub(" ", text)
    text = _EMOJI.sub("", text)
    lowered_map = []
    for word, replacement in _ABBREVIATIONS.items():
        lowered_map.append((re.compile(re.escape(word), re.IGNORECASE), replacement))
    for pattern, replacement in lowered_map:
        text = pattern.sub(replacement, text)
    text = _TIME.sub(_time_to_words, text)
    text = _PERCENT.sub(lambda m: f"{number_to_words(int(m.group(1)))} por ciento", text)
    text = _NUMBER.sub(lambda m: number_to_words(int(m.group(0))), text)
    return _SPACES.sub(" ", text).strip()
