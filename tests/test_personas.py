from callforge.agents.prompts import (
    COMPANION_SYSTEM,
    PERSONAS,
    companion_system,
    parse_persona_command,
    persona_list,
)


def test_parse_command_modo_with_rest():
    assert parse_persona_command("modo cinico cuentame algo") == ("cinico", "cuentame algo")


def test_parse_command_slash_only():
    assert parse_persona_command("/pensador") == ("pensador", "")


def test_parse_command_accent_and_alias():
    # accent-insensitive + alias ("hipotesis" -> pensador)
    assert parse_persona_command("modo hipótesis ¿y si?") == ("pensador", "¿y si?")


def test_parse_command_normal_message_passthrough():
    assert parse_persona_command("hola, como estas?") == (None, "hola, como estas?")


def test_parse_command_modo_normal_reverts():
    assert parse_persona_command("modo normal") == ("companion", "")


def test_companion_system_default_unchanged():
    assert companion_system() == COMPANION_SYSTEM
    assert companion_system(persona="companion") == COMPANION_SYSTEM


def test_companion_system_unknown_persona_falls_back():
    assert companion_system(persona="noexiste") == COMPANION_SYSTEM


def test_companion_system_persona_flavor_and_marker():
    prompt = companion_system(persona="cinico")
    assert "CÍNICO" in prompt
    assert "CompanionAgent" in prompt  # MockProvider keys off this in any mode


def test_persona_list_has_keys():
    keys = {p["key"] for p in persona_list()}
    assert {"companion", "pensador", "cinico", "abogado", "filosofo", "crudo"} <= keys
    assert set(keys) == set(PERSONAS)
