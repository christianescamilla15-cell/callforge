"""Fixed customer-facing phrases. Single source of truth for the cloned
voice bank (scripts/build_voice_bank.py pre-synthesizes ALL of these with
the reference voice into the TTS cache)."""

GREETING = "Hola, qué bueno que estás aquí. Tómate tu tiempo y cuéntame cómo te sientes hoy."

ESCALATED_NOTICE = (
    "Gracias por confiarme esto. Voy a conectarte con una persona que puede "
    "acompañarte mejor; no estás solo en esto."
)

CONTROLLED_FALLBACK = (
    "Perdona, tuve un problema para responderte en este momento. Aquí sigo "
    "contigo; ¿me lo cuentas otra vez cuando puedas?"
)

COULD_NOT_HEAR = "Perdona, no alcancé a escucharte bien. ¿Me lo repites con calma?"

GOODBYE = "Gracias por compartir este rato conmigo. Cuídate mucho; aquí estaré cuando quieras volver."

# Short, warm "I'm-with-you" sounds played the instant a voice turn starts,
# masking the LLM's multi-second latency. Spoken in the same voice, cached.
FILLERS: list[str] = [
    "Mmm, te escucho...",
    "Claro, tómate tu tiempo...",
    "Aquí estoy contigo...",
]

FIXED_PHRASES: dict[str, str] = {
    "greeting": GREETING,
    "escalated_notice": ESCALATED_NOTICE,
    "controlled_fallback": CONTROLLED_FALLBACK,
    "could_not_hear": COULD_NOT_HEAR,
    "goodbye": GOODBYE,
    **{f"filler_{i}": text for i, text in enumerate(FILLERS)},
}
