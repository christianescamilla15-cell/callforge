"""Fixed customer-facing phrases. Single source of truth for the cloned
voice bank (scripts/build_voice_bank.py pre-synthesizes ALL of these with
the reference voice into the TTS cache)."""

GREETING = "Hola, soy el asistente de soporte. Cuéntame qué necesitas y te ayudo a resolverlo."

ESCALATED_NOTICE = (
    "Tu caso ya está con un agente humano y tiene todo el contexto de la "
    "conversación. Te contactará a la brevedad; puedes seguir escribiendo aquí."
)

CONTROLLED_FALLBACK = (
    "Perdona, tuve un problema para responderte en este momento. "
    "¿Puedes intentarlo de nuevo en unos segundos?"
)

COULD_NOT_HEAR = "Perdona, no alcancé a escucharte bien. ¿Me lo repites?"

GOODBYE = "Gracias por comunicarte con soporte. Si necesitas algo más, aquí estaremos."

# Short "I'm-on-it" sounds played the instant a voice turn starts, masking
# the LLM's multi-second latency. Spoken in the same voice, cached.
FILLERS: list[str] = [
    "Déjame revisarlo...",
    "Un momento, por favor...",
    "Claro, lo reviso...",
]

FIXED_PHRASES: dict[str, str] = {
    "greeting": GREETING,
    "escalated_notice": ESCALATED_NOTICE,
    "controlled_fallback": CONTROLLED_FALLBACK,
    "could_not_hear": COULD_NOT_HEAR,
    "goodbye": GOODBYE,
    **{f"filler_{i}": text for i, text in enumerate(FILLERS)},
}
