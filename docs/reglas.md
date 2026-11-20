# Reglas de negocio → pruebas

Ésta es la compuerta real de calidad (más que el % de cobertura): cada regla del dominio tiene al menos una
prueba que falla si la regla se rompe. Las referencias usan `archivo::prueba` desde la raíz del repo;
`tests/test_reglas_doc.py` verifica que cada prueba citada exista, que no esté marcada para saltarse y que
ninguna regla se quede sin prueba (corre en el CI).

Todo corre sin red: el LLM es el `MockProvider` determinista (o `GuionLLM` de `tests/apoyo.py`, que fija qué
contesta cada agente para recorrer las ramas del flujo). Las pruebas que pasan por la API usan SQLite temporal;
`tests/test_postgres.py` repite migraciones y un caso completo contra **PostgreSQL 16** en el CI. Para agregar
una regla: escribe la prueba primero, agrégala a la tabla y corre `pytest`.

## 1. Enrutamiento por intención y urgencia · `orchestration/workflow.py`, `agents/router_agent.py`

El RouterAgent clasifica (intención, categoría, urgencia, frustración) y elige el siguiente agente. Sin LLM, el
`MockProvider` aplica señales por palabras con precedencia **humano > falla técnica > facturación > duda**:
humano = «humano, human, supervisor, gerente, manager, agente real, persona real, hablar con alguien»;
técnico = «error, no funciona, not working, falla, fails, crash, internet, modem, router, lento, slow, no carga,
broken, se cae, intermitente»; facturación = «factura, facturacion, cobro, cargo, billing, charge, refund,
reembolso, pago, payment, invoice». El oráculo del lote golden (`tests/golden/generar_golden.py`) usa esta
misma lista, escrita por separado.

| # | Regla | Pruebas |
|---|---|---|
| R1 | Pedir un humano va a `escalation` con urgencia alta | `tests/test_router_agent.py::test_routes_human_request_to_escalation` |
| R2 | Una falla técnica va a `troubleshooting` | `tests/test_router_agent.py::test_routes_technical_issue_to_troubleshooting`, `tests/test_conversation_flow.py::test_technical_message_uses_troubleshooting_agent` |
| R3 | Facturación va a `support` con intención `billing` | `tests/test_router_agent.py::test_routes_billing_to_support` |
| R4 | Lo demás va a `support` como `question` y la confianza está en [0, 1] | `tests/test_conversation_flow.py::test_full_conversation_flow_persists_messages`, `tests/test_router_agent.py::test_router_reports_confidence` |
| R5 | Con señales mezcladas manda la más fuerte: humano > técnico > facturación > duda | `tests/test_reglas_flujo.py::test_precedencia_del_enrutamiento_humano_tecnico_facturacion` |
| R6 | Transición inválida: un `next_agent` fuera del catálogo (incluido el retirado `companion`) se atiende como `support` | `tests/test_reglas_flujo.py::test_el_router_no_puede_elegir_un_agente_fuera_del_catalogo` |
| R7 | Intención o urgencia fuera del catálogo caen a `other`/`medium`; categoría vacía, a `general` | `tests/test_reglas_flujo.py::test_intencion_y_urgencia_invalidas_caen_a_other_y_medium` |
| R8 | La intención, categoría y urgencia del router quedan en el resultado del turno | `tests/test_reglas_flujo.py::test_la_urgencia_del_router_viaja_al_resultado` |
| R9 | La base de conocimiento se consulta sólo para `support` y `troubleshooting`, no al escalar | `tests/test_reglas_flujo.py::test_el_conocimiento_se_consulta_solo_para_support_y_troubleshooting` |

## 2. Calidad: reintento por baja calificación · `orchestration/policies.py`

El QualityAgent califica la respuesta candidata; con calificación **menor** que `QUALITY_THRESHOLD` (0.5) y
reintentos disponibles (`MAX_QUALITY_RETRIES`, 1) el especialista lo intenta otra vez con la retroalimentación.

| # | Regla | Pruebas |
|---|---|---|
| Q1 | Calificación en o sobre el umbral: sin reintento | `tests/test_reglas_flujo.py::test_calidad_suficiente_no_reintenta`, `tests/test_escalation.py::test_escalation_policy_thresholds` |
| Q2 | Calificación baja: un reintento, con evento `retried` y los problemas señalados en la entrada del especialista | `tests/test_reglas_flujo.py::test_calidad_baja_reintenta_una_vez_con_la_retroalimentacion` |
| Q3 | Si sigue baja al agotar los reintentos, se escala | `tests/test_reglas_flujo.py::test_calidad_baja_tras_agotar_reintentos_escala`, `tests/test_reglas_flujo.py::test_sin_reintentos_configurados_la_calidad_baja_escala_de_inmediato` |
| Q4 | Si el QualityAgent falla, no se reintenta y la respuesta sale sin calificación | `tests/test_reglas_flujo.py::test_si_el_quality_agent_falla_no_se_reintenta_y_la_respuesta_sale` |

## 3. Escalamiento a un humano · `orchestration/workflow.py`, `orchestration/policies.py`

| # | Regla | Pruebas |
|---|---|---|
| E1 | El router escala directo: EscalationAgent + SummarizerAgent, sin especialista ni calidad; ticket con título `[categoría] mensaje` | `tests/test_reglas_flujo.py::test_el_router_escala_directo_sin_especialista_ni_calidad` |
| E2 | Escalar crea Ticket + Escalation ligados y deja la conversación ESCALATED | `tests/test_escalation.py::test_human_request_escalates_and_creates_ticket` |
| E3 | El especialista que sugiere escalar, escala | `tests/test_reglas_flujo.py::test_el_especialista_que_sugiere_escalar_escala`, `tests/test_escalation.py::test_escalation_policy_thresholds` |
| E4 | Confianza del especialista menor que `CONFIDENCE_THRESHOLD` escala; en el umbral, no | `tests/test_reglas_flujo.py::test_confianza_baja_del_especialista_escala`, `tests/test_reglas_flujo.py::test_confianza_en_el_umbral_no_escala` |
| E5 | La prioridad del ticket es la del EscalationAgent; una inválida o ausente cae a `medium` | `tests/test_reglas_flujo.py::test_la_prioridad_del_ticket_sale_del_escalamiento_y_la_invalida_cae_a_medium` |
| E6 | El resumen del SummarizerAgent queda en la conversación escalada | `tests/test_reglas_flujo.py::test_el_resumen_del_summarizer_queda_en_el_resultado_del_escalamiento` |
| E7 | Una conversación escalada es del humano: se guarda el mensaje, el bot no corre, no hay ticket nuevo | `tests/test_audit_fixes.py::test_escalated_conversation_skips_bot_and_keeps_message` |

## 4. Ciclo de vida de la conversación (máquina de estados) · `domain/lifecycle.py`

Ver [ADR 0003](adr/0003-ciclo-de-vida-de-la-conversacion.md). ACTIVE → ESCALATED / RESOLVED / CLOSED;
RESOLVED → ACTIVE (el cliente vuelve a escribir) / CLOSED; ESCALATED → RESOLVED / CLOSED; CLOSED es terminal.

| # | Regla | Pruebas |
|---|---|---|
| L1 | Tabla completa: 10 transiciones permitidas y 6 inválidas (ESCALATED→ACTIVE, RESOLVED→ESCALATED, todo desde CLOSED); una inválida no deja rastro | `tests/test_ciclo_de_vida.py::test_tabla_completa_de_transiciones`, `tests/test_ciclo_de_vida.py::test_una_transicion_real_actualiza_la_fecha_y_la_misma_no` |
| L2 | Una conversación nueva empieza ACTIVE | `tests/test_ciclo_de_vida.py::test_una_conversacion_nueva_empieza_activa` |
| L3 | La calificación «resuelta» lleva ACTIVE → RESOLVED; sin «resuelta» no cambia nada | `tests/test_api.py::test_feedback_marks_conversation_resolved`, `tests/test_ciclo_de_vida.py::test_calificar_sin_resolver_no_cambia_el_estado` |
| L4 | Cerrar lleva a RESOLVED o CLOSED; CLOSED rechaza mensajes con 409 | `tests/test_audit_fixes.py::test_close_conversation_endpoint` |
| L5 | CLOSED es terminal: no se reabre, no se cierra otra vez (409) y la calificación no la mueve | `tests/test_ciclo_de_vida.py::test_cerrada_es_terminal_no_se_reabre_ni_se_cierra_otra_vez` |
| L6 | ESCALATED no vuelve al bot ni la resuelve la calificación; el humano la cierra | `tests/test_ciclo_de_vida.py::test_escalada_no_vuelve_al_bot_ni_la_resuelve_la_calificacion`, `tests/test_ciclo_de_vida.py::test_el_agente_humano_cierra_la_escalada_como_resuelta` |
| L7 | RESOLVED se reabre (ACTIVE) si el cliente vuelve a escribir, y puede volver a escalar | `tests/test_ciclo_de_vida.py::test_resuelta_se_reabre_si_el_cliente_vuelve_a_escribir`, `tests/test_ciclo_de_vida.py::test_resuelta_que_se_reabre_y_pide_humano_queda_escalada` |
| L8 | RESOLVED puede archivarse como CLOSED; resolverla otra vez es idempotente | `tests/test_ciclo_de_vida.py::test_resuelta_puede_archivarse_como_cerrada` |
| L9 | Operar sobre una conversación inexistente da 404 | `tests/test_ciclo_de_vida.py::test_operar_sobre_una_conversacion_inexistente_da_404`, `tests/test_conversation_flow.py::test_message_to_unknown_conversation_returns_404` |
| L10 | El ticket acepta open/in_progress/resolved/closed en cualquier orden; un estado desconocido da 422 | `tests/test_audit_fixes.py::test_ticket_status_update` |

## 5. Diagnóstico guiado (pasos de resolución) · `application/use_cases/send_message.py`

| # | Regla | Pruebas |
|---|---|---|
| D1 | Troubleshooting propone un paso numerado que se guarda PROPOSED | `tests/test_resolution_steps.py::test_diagnostic_steps_persist_across_messages`, `tests/test_resolution_steps.py::test_workflow_returns_resolution_step_entity` |
| D2 | El siguiente mensaje del cliente responde el último paso pendiente (ANSWERED, con su respuesta) | `tests/test_resolution_steps.py::test_diagnostic_steps_persist_across_messages` |
| D3 | El agente ve los pasos previos con su resultado y numera después de ellos | `tests/test_resolution_steps.py::test_troubleshooting_prompt_includes_previous_steps`, `tests/test_reglas_flujo.py::test_troubleshooting_numera_el_paso_despues_de_los_previos` |
| D4 | Un paso sin instrucción (vacía, «null») no se registra; support nunca propone pasos | `tests/test_reglas_flujo.py::test_un_paso_sin_instruccion_no_se_registra`, `tests/test_reglas_flujo.py::test_support_nunca_propone_pasos_de_diagnostico`, `tests/test_resolution_steps.py::test_non_technical_conversation_has_no_steps` |

## 6. Multi-tenant y acceso · `presentation/api/deps.py`, `infrastructure/repositories.py`

| # | Regla | Pruebas |
|---|---|---|
| T1 | Un tenant no ve la conversación, los tickets ni las métricas de otro | `tests/test_multi_tenant.py::test_tenants_are_isolated`, `tests/test_aislamiento_tenants.py::test_listados_solo_traen_lo_del_tenant` |
| T2 | La API key de un tenant no sirve para escribir en datos de otro (mensaje, cierre, calificación, ticket: 404) y las métricas del dueño no cambian | `tests/test_aislamiento_tenants.py::test_la_api_key_de_un_tenant_no_sirve_para_tocar_datos_de_otro` |
| T3 | La base de conocimiento es por tenant | `tests/test_aislamiento_tenants.py::test_la_base_de_conocimiento_es_por_tenant` |
| T4 | El webchat no reanuda una conversación de otro tenant | `tests/test_aislamiento_tenants.py::test_el_webchat_no_reanuda_una_conversacion_de_otro_tenant` |
| T5 | El mismo `customer_external_id` es un cliente distinto por tenant y el mismo dentro de uno | `tests/test_multi_tenant.py::test_tenants_are_isolated`, `tests/test_conversation_flow.py::test_reusing_external_id_reuses_customer` |
| T6 | Una credencial desconocida da 401; la clave vacía del tenant por omisión no es credencial | `tests/test_multi_tenant.py::test_unknown_api_token_is_rejected`, `tests/test_aislamiento_tenants.py::test_la_clave_vacia_del_tenant_por_omision_no_es_una_credencial` |
| T7 | Con `API_TOKEN` la API exige la cabecera; sin tokens, todo va al tenant por omisión; `/health` queda abierto | `tests/test_api.py::test_api_token_gate`, `tests/test_multi_tenant.py::test_default_tenant_open_mode_still_works` |
| T8 | El alta de tenants exige `ADMIN_TOKEN` (403 sin configurar, 401 si no coincide) | `tests/test_multi_tenant.py::test_admin_api_disabled_without_admin_token`, `tests/test_multi_tenant.py::test_admin_requires_correct_token` |
| T9 | Matriz perfil × ruta (anónimo, ajena, global, operador, administrador) con el código esperado | `tests/e2e/test_soporte_por_rol.py::test_matriz_de_acceso_por_perfil` |
| T10 | El webchat exige la credencial cuando hay `API_TOKEN` y rechaza una inválida | `tests/test_webchat.py::test_webchat_requires_token_when_configured`, `tests/e2e/test_soporte_por_rol.py::test_el_webchat_rechaza_una_credencial_invalida` |

## 7. Cadena de proveedores LLM, respaldo y costo · `infrastructure/llm/`

Orden por omisión **Groq → Ollama → Mock**; con `LLM_PRIMARY=ollama`, **Ollama remoto → Ollama local → Groq →
Mock**. Ver [ADR 0002](adr/0002-cadena-de-proveedores-llm.md).

| # | Regla | Pruebas |
|---|---|---|
| F1 | Orden de la cadena según `LLM_PRIMARY` (groq por omisión) | `tests/test_llm_fallback.py::test_llm_primary_ollama_puts_ollama_first`, `tests/test_llm_fallback.py::test_remote_brain_is_first_with_local_fallback` |
| F2 | Contesta el primero sano; si cae, el siguiente | `tests/test_llm_fallback.py::test_first_provider_wins_when_healthy`, `tests/test_llm_fallback.py::test_falls_back_to_next_provider`, `tests/test_llm_fallback.py::test_remote_brain_down_falls_through_to_local`, `tests/test_llm_fallback.py::test_provider_stream_default_yields_full_text` |
| F3 | Todos caídos: `LLMUnavailableError` con los errores en orden de la cadena | `tests/test_llm_fallback.py::test_raises_when_all_providers_fail` |
| F4 | Todos caídos en la API: 200 con el mensaje controlado, evento `fallback_used`, error trazado, sin ticket | `tests/test_llm_fallback.py::test_todos_los_proveedores_caidos_la_api_responde_con_el_mensaje_controlado` |
| F5 | Un fallo en router, especialista o reintento da la respuesta controlada con las corridas ya hechas; una respuesta vacía también | `tests/test_reglas_flujo.py::test_un_fallo_de_llm_en_cualquier_paso_da_la_respuesta_controlada`, `tests/test_reglas_flujo.py::test_una_respuesta_vacia_del_especialista_usa_la_respuesta_controlada` |
| F6 | El Mock es la red final aunque esté desactivado si no hay otro; con Groq y `MOCK_ENABLED=false` no entra; la cadena vacía no existe | `tests/test_llm_fallback.py::test_el_mock_es_la_red_final_aunque_este_desactivado_si_no_hay_otro`, `tests/test_llm_fallback.py::test_la_cadena_vacia_no_se_puede_construir` |
| F7 | Costo = tokens × precio publicado por millón; modelos locales o desconocidos cuestan 0 | `tests/test_llm_fallback.py::test_costo_estimado_por_millon_de_tokens_y_modelo_desconocido_gratis`, `tests/test_paridad_golden.py::test_consumo_del_lote_en_decimal` |
| F8 | Cada corrida de agente y su uso de LLM se persisten y se agregan en `/metrics` | `tests/test_api.py::test_metrics_after_traffic`, `tests/test_paridad_golden.py::test_cifras_control_del_lote` |

## 8. Conocimiento (RAG híbrido) · `infrastructure/knowledge/store.py`

| # | Regla | Pruebas |
|---|---|---|
| K1 | Búsqueda por palabras: el más relevante primero, sin importar acentos; sin coincidencias, vacío | `tests/test_knowledge.py::test_retrieval_ranks_relevant_document_first`, `tests/test_knowledge.py::test_retrieval_handles_accents`, `tests/test_knowledge.py::test_retrieval_returns_empty_for_unrelated_query` |
| K2 | Con embeddings rankea por significado y filtra bajo `KNOWLEDGE_MIN_SIMILARITY` | `tests/test_knowledge.py::test_hybrid_store_ranks_by_meaning_without_shared_words`, `tests/test_knowledge.py::test_hybrid_store_filters_below_min_similarity` |
| K3 | Embedder caído, ausente o de otro modelo (dimensiones distintas): cae a búsqueda por palabras | `tests/test_knowledge.py::test_hybrid_store_falls_back_to_keyword_when_embedder_fails`, `tests/test_knowledge.py::test_hybrid_store_without_embedder_uses_keyword`, `tests/test_knowledge.py::test_hybrid_store_with_vectors_of_another_model_falls_back_to_keyword` |
| K4 | Los documentos sin embedding se rellenan y persisten en la primera búsqueda | `tests/test_knowledge.py::test_hybrid_store_backfills_missing_embeddings` |
| K5 | La carga por API crea el documento | `tests/test_knowledge.py::test_knowledge_endpoint_creates_document` |

## 9. Persistencia y migraciones · `infrastructure/database.py`, `migrations/`

| # | Regla | Pruebas |
|---|---|---|
| M1 | Una base nueva migra a head; correr las migraciones dos veces no hace nada | `tests/test_migrations.py::test_fresh_database_migrates_to_head`, `tests/test_migrations.py::test_run_migrations_is_idempotent` |
| M2 | Una base anterior a Alembic se sella en la línea base y sube | `tests/test_migrations.py::test_pre_alembic_database_is_stamped_then_upgraded` |
| M3 | 0006 retira la tabla del modo companion; su downgrade la recrea igual que 0005 (upgrade → downgrade → upgrade) | `tests/test_migrations.py::test_head_no_longer_has_the_companion_table`, `tests/test_migrations.py::test_companion_drop_round_trips_upgrade_downgrade_upgrade`, `tests/test_postgres.py::test_postgres_migra_a_head_y_la_0006_va_y_vuelve` |
| M4 | El histórico que nombra un agente retirado se lee (agente vacío) sin tumbar la conversación | `tests/test_migrations.py::test_history_naming_a_retired_agent_still_reads` |
| M5 | Todo funciona igual en PostgreSQL 16 (caso completo y aislamiento) | `tests/test_postgres.py::test_postgres_caso_completo_y_aislamiento_por_tenant` |
| M6 | Listados filtran por estado y paginan | `tests/test_audit_fixes.py::test_list_conversations_with_filter_and_pagination` |

## 10. Voz · `infrastructure/voice/`, `presentation/api/routes/voice.py`

| # | Regla | Pruebas |
|---|---|---|
| V1 | Un turno de voz: STT → el mismo flujo de soporte → audio; con `stream` sólo la primera frase va en audio | `tests/test_voice.py::test_voice_message_full_turn`, `tests/test_voice.py::test_voice_message_stream_mode_returns_sentence_pipeline`, `tests/test_voice.py::test_voice_message_default_returns_full_audio` |
| V2 | Sin TTS o sin STT: 503; audio vacío: 422 | `tests/test_voice.py::test_tts_disabled_returns_503`, `tests/test_voice.py::test_voice_message_without_stt_returns_503`, `tests/test_voice.py::test_voice_message_empty_audio_rejected` |
| V3 | Los motores de voz degradan al siguiente (Cartesia → Kokoro, convertidor → Kokoro) y fallan sólo si todos fallan | `tests/test_voice.py::test_fallback_tts_degrades_to_next_engine`, `tests/test_voice.py::test_fallback_tts_raises_when_all_fail`, `tests/test_voice.py::test_cartesia_falls_back_to_local_when_cloud_down`, `tests/test_voice.py::test_cloned_live_tts_degrades_to_inner_when_converter_down` |
| V4 | El audio se cachea por texto normalizado y voz; el texto se normaliza para hablarse | `tests/test_voice.py::test_cached_tts_serves_from_disk_and_normalizes`, `tests/test_tts_normalize.py::test_normalize_expands_numbers_and_percent`, `tests/test_tts_normalize.py::test_normalize_strips_markdown_urls_and_emoji` |
| V5 | Clonar una voz exige nombre seguro y audio suficiente; a ffmpeg sólo llega una extensión de audio conocida | `tests/test_voice.py::test_upload_voice_rejects_bad_name_and_short_audio`, `tests/test_voice.py::test_upload_voice_wav_and_list`, `tests/test_voice.py::test_upload_voice_only_hands_ffmpeg_a_known_audio_extension` |
| V6 | El mensaje de voz entrante se guarda en el buzón (referencia de clonación y QA) | `tests/test_voice.py::test_voice_message_persists_audio_to_inbox` |

## 11. Paridad, contrato y recorrido de punta a punta

| # | Regla | Pruebas |
|---|---|---|
| P1 | El lote golden coincide con su oráculo independiente, fila por fila y por hash | `tests/test_paridad_golden.py::test_el_golden_esta_al_dia_con_su_oraculo`, `tests/test_paridad_golden.py::test_el_hash_del_lote_coincide_entre_oraculo_y_comparador`, `tests/test_paridad_golden.py::test_paridad_del_lote_contra_el_golden` |
| P2 | Cifras control del lote (corridas, eventos, mensajes, tickets, pasos, estados) y orden por inversiones | `tests/test_paridad_golden.py::test_cifras_control_del_lote`, `tests/test_paridad_golden.py::test_el_orden_de_operacion_no_cambia_el_resultado_y_los_turnos_quedan_en_orden` |
| P3 | Control negativo: las diferencias sembradas se detectan exactas | `tests/test_paridad_golden.py::test_control_negativo_un_esperado_alterado_da_exactamente_las_diferencias_sembradas`, `tests/test_paridad_golden.py::test_control_negativo_una_entrada_alterada_cambia_solo_su_conversacion`, `tests/test_paridad_golden.py::test_control_negativo_las_inversiones_cuentan_desplazamientos_no_posiciones` |
| P4 | El contrato OpenAPI versionado está al día y cada operación está documentada | `tests/test_openapi.py::test_el_contrato_versionado_esta_al_dia`, `tests/test_openapi.py::test_el_contrato_trae_todas_las_rutas_de_negocio`, `tests/test_openapi.py::test_cada_operacion_esta_documentada` |
| P5 | Un caso de soporte completo por rol (alta, conocimiento, webchat, diagnóstico, escalamiento, ticket, cierre, métricas) | `tests/e2e/test_soporte_por_rol.py::test_flujo_completo_de_un_caso_de_soporte`, `tests/e2e/test_soporte_por_rol.py::test_el_cliente_por_rest_recibe_respuesta_y_la_conversacion_reanuda_en_el_webchat`, `tests/test_webchat.py::test_webchat_full_conversation`, `tests/test_audit_fixes.py::test_webchat_resumes_existing_conversation` |
