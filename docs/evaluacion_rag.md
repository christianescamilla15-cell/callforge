# Evaluación del RAG

Cómo se mide la recuperación de conocimiento de CallForge y la calidad de las respuestas que se apoyan en ella,
con qué cifras, y qué compuerta las cuida en el CI. El bloque de cifras de la sección 4 lo escribe
`python -m callforge.evaluation.rag --informe` y el CI exige que esté al día (`--revisar-informe`): ninguna cifra
de esa sección está escrita a mano.

## 1. Qué se evalúa

El turno de soporte busca en la base de conocimiento del tenant (`HybridKnowledgeStore`: embeddings
`nomic-embed-text` con coseno y piso de similitud 0.55; si el embedder falla, palabras clave) y le pasa los 3
mejores fragmentos al `SupportAgent`, que debe responder SÓLO con ellos y, desde este cambio, devolver en
`citations` los números de los fragmentos que respaldan su respuesta. Se mide:

1. **Recuperación**, sin LLM: hit@1/3/5, MRR@10 y **cobertura de citas@3** (qué parte de las fuentes que una
   respuesta necesita quedó entre los 3 fragmentos que puede citar). Se compara el híbrido de producción con su
   caída a palabras clave, por tipo de pregunta.
2. **Respuestas**, con verificación determinista: **fidelidad** (cada afirmación —oración declarativa con al
   menos tres palabras de contenido— debe estar respaldada por los fragmentos que la respuesta CITA: ≥ 60 % de
   sus raíces y TODAS sus cifras aparecen ahí), citas válidas y fantasma, si cita la fuente esperada,
   **exactitud** contra la referencia (hechos clave presentes y F1 de tokens) y abstención (fuera de dominio
   debe abstenerse; con respuesta, no).
3. **Juez LLM opcional**: un modelo local dice, afirmación por afirmación, si los fragmentos citados la
   respaldan; se reporta su acuerdo con el verificador determinista. Nunca entra a la compuerta.

## 2. El conjunto de evaluación (`evaluacion/rag/`)

- `corpus.json`: 40 documentos de una base de soporte de un proveedor de internet, telefonía y TV en casa.
  La base del repo sólo traía dos documentos de ejemplo (`scripts/e2e_probe.py`: reiniciar el módem y política
  de facturación), que entran tal cual (`origen: repo`). Los otros 38 son **sintéticos**, coherentes con ese
  dominio (módem y luces, wifi, velocidad, fallas masivas, visitas técnicas, planes, pagos, recargos y
  reconexión, aclaraciones, reembolsos de saldo, facturación electrónica, cancelación y equipos, cuenta y
  seguridad, quejas, compensaciones…), con cifras concretas y **distractores** deliberados (pago domiciliado /
  en tienda / en línea; costo de la visita / cancelarla; wifi lento / prueba de velocidad / cambio de plan).
- `preguntas.json`: 86 preguntas, cada una con sus fuentes esperadas, una respuesta de referencia y sus hechos
  clave (frases que aparecen TEXTUALMENTE en la fuente): 32 **literales** (usan las palabras del artículo), 32
  **paráfrasis** (lo mismo con otras palabras: «el aparatito del wifi»; casi sin palabras de contenido en común
  con su fuente: el traslape medido está en la sección 4), 14 **multi** (necesitan dos documentos) y 8 **fuera
  de dominio** (la base no tiene la respuesta: lo correcto es abstenerse).
- `embeddings/nomic-embed-text.json`: los vectores reales de cada fragmento y cada pregunta (float16, llave
  `sha256(modelo + texto)`), para que el CI mida el híbrido sin red. Un texto editado o de otro modelo es un
  fallo de caché, y como el híbrido degradaría EN SILENCIO a palabras clave, la corrida los cuenta y la
  compuerta rechaza cualquier fallo.
- `compuerta.json`: umbrales y relaciones de la compuerta. `respuestas/`: instantánea versionada de las
  respuestas de un LLM local (sección 5).

`tests/test_evaluacion_rag.py` valida el conjunto (fuentes que existen, hechos textuales en su fuente, todo
documento usado como fuente) y demuestra que el validador detecta uno roto.

### De dónde salen los vectores

En esta máquina no hay Ollama, así que los vectores se calcularon con **el mismo modelo que Ollama sirve como
`nomic-embed-text`** (nomic-embed-text-v1.5, pesos f16 en GGUF) con `llama-cpp-python` en proceso
(`scripts/evaluar_rag_local.py embeddings`). Comprobación de equivalencia: los cuatro pares del sondeo en vivo
contra Ollama que calibró el piso en junio (`scripts/probe_embeddings.py`, «relevantes ~0.7+, no relacionados
~0.45–0.52») dan aquí 0.732 (internet ↔ módem), 0.500, 0.509 y 0.460. Con Ollama disponible, el mismo caché se
regenera contra el servidor real: `python -m callforge.evaluation.rag embeddings --url http://localhost:11434
--motor "ollama <versión>"`.

## 3. Cómo se corre

```bash
PYTHONPATH=src python -m callforge.evaluation.rag                       # mide e imprime todo
PYTHONPATH=src python -m callforge.evaluation.rag --compuerta           # sale con 1 bajo los umbrales (CI)
PYTHONPATH=src python -m callforge.evaluation.rag --sabotaje embeddings_barajados --compuerta   # debe salir 1
PYTHONPATH=src python -m callforge.evaluation.rag --informe             # reescribe la sección 4
# con un modelo (manual, nunca en el CI)
PYTHONPATH=src python scripts/evaluar_rag_local.py llm --gguf <modelo.gguf> --modelo <nombre> --juez
```

Top-k (3) y piso (0.55) se leen de los valores por omisión de `Settings`: la evaluación mide la configuración
de producción, no una copia.

## 4. Resultados

<!-- metricas:inicio -->

Conjunto `2026-11-25/2026-11-25`: 40 documentos, 86 preguntas (32 literales, 32 paráfrasis, 14 multi, 8 fuera de dominio); raíces de contenido que comparte una pregunta con su fuente, en promedio: literales 4.72, paráfrasis 0.34, multi 5.5. top-k = 3, piso de similitud = 0.55, embeddings `nomic-embed-text` (llama-cpp-python 0.3.38 (CPU, en proceso) · nomic-embed-text-v1.5.f16.gguf).

**Recuperación** (preguntas con respuesta; la abstención es sobre las de fuera de dominio)

| Recuperador | hit@1 | hit@3 | hit@5 | MRR@10 | cobertura de citas@3 | sin resultados | hit@3 literal | hit@3 paráfrasis | hit@3 multi | abstención fuera de dominio |
|---|---|---|---|---|---|---|---|---|---|---|
| Palabras clave (caída) | 51.3 % | 56.4 % | 59.0 % | 0.542 | 55.1 % | 2.6 % | 100.0 % | 0.0 % | 85.7 % | 0.0 % |
| Híbrido (embeddings, piso 0.55) | 59.0 % | 64.1 % | 69.2 % | 0.636 | 62.8 % | 0.0 % | 100.0 % | 15.6 % | 92.9 % | 12.5 % |
| Embeddings sin piso (referencia) | 59.0 % | 64.1 % | 69.2 % | 0.636 | 62.8 % | 0.0 % | 100.0 % | 15.6 % | 92.9 % | 0.0 % |
| Fusión RRF palabras + embeddings (referencia) | 52.6 % | 59.0 % | 60.3 % | 0.563 | 57.0 % | 0.0 % | 100.0 % | 0.0 % | 100.0 % | 0.0 % |
| Candidato `bge-m3`, sin piso (referencia) | 79.5 % | 93.6 % | 98.7 % | 0.872 | 92.3 % | 0.0 % | 100.0 % | 84.4 % | 100.0 % | 0.0 % |
| Candidato `bge-m3`, piso 0.55 (referencia) | 70.5 % | 75.6 % | 76.9 % | 0.733 | 74.4 % | 19.2 % | 100.0 % | 40.6 % | 100.0 % | 75.0 % |
| Control: embeddings barajados | 3.9 % | 10.3 % | 17.9 % | 0.099 | 9.6 % | 0.0 % | 3.1 % | 18.8 % | 7.1 % | 12.5 % |
| Control: recuperador al azar | 1.3 % | 10.3 % | 14.1 % | 0.072 | 8.3 % | 0.0 % | 6.2 % | 9.4 % | 21.4 % | 0.0 % |

**¿Dónde podría cortar un piso de similitud?** (mejor coseno de cada pregunta, sin piso)

| Embeddings | mínimo con respuesta | mediana con respuesta | máximo fuera de dominio |
|---|---|---|---|
| `nomic-embed-text` | 0.561 | 0.734 | 0.718 |
| `bge-m3` | 0.439 | 0.673 | 0.629 |

**Respuestas** (fidelidad y citas verificadas sin LLM contra los fragmentos citados)

| Generador | fidelidad | respuestas 100 % fieles | con cita válida | cita la fuente esperada | hechos clave | F1 vs. referencia | abstención fuera de dominio | abstención indebida | citas fantasma |
|---|---|---|---|---|---|---|---|---|---|
| Extractivo sobre el híbrido | 100.0 % | 100.0 % | 100.0 % | 60.3 % | 48.1 % | 31.8 % | 12.5 % | 0.0 % | 0 |
| Extractivo sobre palabras clave | 100.0 % | 100.0 % | 100.0 % | 56.4 % | 42.3 % | 28.6 % | 0.0 % | 2.6 % | 0 |
| Control: cifras alteradas (extractivo) | 40.4 % | 11.5 % | 100.0 % | 60.3 % | 9.6 % | 27.4 % | 12.5 % | 0.0 % | 0 |

**Compuerta** (`evaluacion/rag/compuerta.json`)

| Corrida | Resultado |
|---|---|
| Medición real | pasa |
| Control: embeddings barajados | FALLA (9 umbrales) |
| Control: recuperador al azar | FALLA (12 umbrales) |
| Control: cifras alteradas | FALLA (2 umbrales) |

<!-- metricas:fin -->

## 5. Hallazgos

1. **Las paráfrasis son el punto ciego del embedder de producción.** Con las palabras del artículo, los dos
   recuperadores aciertan el 100 % en el top-3. Con otras palabras, la búsqueda por palabras clave cae a 0 %
   (no hay qué empatar) y `nomic-embed-text` sólo sube a 15.6 % (5 de 32): lo mismo que el azar (9.4 % el
   recuperador aleatorio, 18.8 % con los vectores barajados). La ventaja del híbrido sobre su caída (64.1 %
   contra 56.4 % en hit@3; MRR 0.636 contra 0.542) viene sobre todo de las preguntas «multi» y de unas pocas
   paráfrasis. nomic-embed-text-v1.5 se entrenó casi sólo en inglés; con español coloquial no capta el
   significado.
2. **El piso de 0.55 no separa.** Sólo rechaza 1 de las 8 preguntas fuera de dominio (12.5 %) y no le cuesta
   nada a las que tienen respuesta. La tabla de separación lo explica: con nomic, la mejor similitud de una
   pregunta fuera de dominio llega a 0.718 y la de una con respuesta baja a 0.561 (mediana 0.734); los rangos se
   enciman y no hay umbral que los separe. El piso se calibró en junio con cuatro pares y dos documentos.
3. **Un embedder multilingüe sí resuelve las paráfrasis.** Medido como referencia con el mismo conjunto,
   `bge-m3` (multilingüe; Ollama lo sirve como `bge-m3`) llega a 93.6 % en hit@3, 84.4 % en paráfrasis, MRR
   0.872 y cobertura de citas de 92.3 %. Su escala de similitud es otra: con el piso de 0.55 se abstiene en 6 de
   8 fuera de dominio pero deja sin resultados al 19.2 % de las que sí tienen respuesta, así que el piso se
   tiene que recalibrar por modelo (en bge-m3 tampoco separa del todo: 0.439 contra 0.629). La fusión RRF de
   palabras clave y nomic no ayuda (59.0 % en hit@3, 0 % en paráfrasis: el ruido léxico empuja hacia abajo los
   aciertos semánticos), y en una prueba exploratoria fuera del conjunto versionado los prefijos de tarea de
   nomic (`search_query:` / `search_document:`) dejaron las paráfrasis en 12.5 %.
4. **Siguiente paso recomendado, no aplicado aquí:** cambiar `EMBEDDING_MODEL` a `bge-m3` con un piso
   recalibrado sobre este conjunto. No es sólo una variable: los vectores guardados de otro modelo (768 contra
   1024 dimensiones) hacen caer la búsqueda a palabras clave (regla K3) y el relleno perezoso sólo embebe los
   documentos SIN vector, así que hace falta re-embeber la base de cada tenant. Es una decisión de producción;
   esta evaluación es la que la justificaría y la que diría si funcionó.
5. **El extractivo es la línea base, no la meta.** Copia oraciones de los fragmentos, así que su fidelidad es
   100 % por construcción; lo que mide es el camino recuperación → respuesta: cita la fuente esperada en 60.3 %
   y trae los hechos clave en 48.1 %, acotado por el 64.1 % de hit@3 y por la elección de oraciones.
6. **Los controles negativos funcionan.** Con los vectores barajados el híbrido cae a 10.3 % en hit@3 y la
   compuerta falla en 9 umbrales; con un recuperador al azar, en 12; con las cifras de las respuestas movidas
   en uno (60 → 61), la fidelidad baja de 100 % a 40.4 % y la compuerta falla.

## 6. Compuerta del CI

`evaluacion/rag/compuerta.json` fija, sobre métricas deterministas: hit@3, MRR@10, cobertura de citas@3 y hit@3
literal de los dos recuperadores; además hit@3 de paráfrasis y multi del híbrido; y fidelidad, citas válidas,
cita de la fuente esperada y hechos clave del extractivo sobre el híbrido. Cada umbral está a unos 0.02 de lo
medido (una prueba exige que ninguno quede más de 0.10 abajo, para que no sean de adorno). Dos relaciones
exigen que el híbrido no sea peor que su caída a palabras clave en hit@3 y MRR. La compuerta también rechaza
la corrida si el híbrido tuvo un solo fallo de caché.

El job `pruebas` del CI corre la compuerta, corre el control negativo (`--sabotaje embeddings_barajados
--compuerta` DEBE salir con 1) y revisa que este informe esté al día. `tests/test_evaluacion_rag.py` repite
los tres sabotajes y verifica que cada uno tumbe el umbral que le corresponde.

Los umbrales se suben cuando la medición sube (p. ej. al adoptar otro embedder); bajarlos exige explicarlo en
este informe.

## 7. Límites de esta evaluación

- El corpus es sintético salvo dos documentos, redactado con ayuda de un asistente de IA y validado por
  script (fuentes, hechos textuales, cobertura, traslape). Mide la recuperación en este dominio y en español;
  no es una muestra de conversaciones reales.
- Cada documento es un fragmento (de 33 a 111 palabras): no se evalúa la partición en fragmentos.
- Con 32 preguntas por tipo, cada pregunta mueve 3.1 puntos; las diferencias pequeñas entre configuraciones no
  son significativas. Las grandes (15.6 % contra 84.4 % en paráfrasis) sí.
- Los hechos clave se comparan textualmente: una respuesta correcta que dice «un minuto» en vez de «60
  segundos» cuenta como faltante. El F1 contra la referencia compensa en parte.
- El verificador de fidelidad es léxico: una afirmación que niega su fuente con las mismas palabras («no se
  cobra» contra «se cobra») puede pasar. Las cifras sí se exigen exactas. Para lo demás está el juez.
- Los vectores se calcularon con llama.cpp en proceso, no con el servidor de Ollama; la equivalencia se
  comprobó con los pares del sondeo de junio (sección 2).
