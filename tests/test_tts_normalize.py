from callforge.infrastructure.voice.normalize import normalize_for_tts, number_to_words


def test_numbers_to_spanish_words():
    assert number_to_words(0) == "cero"
    assert number_to_words(10) == "diez"
    assert number_to_words(21) == "veintiuno"
    assert number_to_words(45) == "cuarenta y cinco"
    assert number_to_words(100) == "cien"
    assert number_to_words(157) == "ciento cincuenta y siete"
    assert number_to_words(500) == "quinientos"
    assert number_to_words(1000) == "mil"
    assert number_to_words(2024) == "dos mil veinticuatro"


def test_normalize_expands_numbers_and_percent():
    out = normalize_for_tts("Puedes pagar hasta el dia 10 sin recargo del 5%")
    assert "diez" in out and "cinco por ciento" in out
    assert "10" not in out and "5" not in out


def test_normalize_times():
    out = normalize_for_tts("El soporte abre a las 9:30 y cierra a las 18:00")
    assert "nueve treinta" in out
    assert "dieciocho en punto" in out


def test_normalize_strips_markdown_urls_and_emoji():
    out = normalize_for_tts(
        "**Importante**: revisa [la guia](https://x.com/guia) o https://ayuda.com 🙂"
    )
    assert "*" not in out and "https" not in out and "🙂" not in out
    assert "la guia" in out


def test_normalize_abbreviations():
    out = normalize_for_tts("Sr. Lopez, espere 5 min. por favor")
    assert "señor" in out and "minutos" in out
