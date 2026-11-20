# Seguridad

## Reportar una vulnerabilidad

Escribe a **christianescamilla15@gmail.com** con el asunto «Seguridad: CallForge». Respondo en 72 horas
hábiles y publico el arreglo con su aviso. Por favor no abras un issue público para vulnerabilidades.

## Qué revisa cada cambio

| Revisión | Herramienta | Bloquea |
|---|---|---|
| Análisis estático | CodeQL (Python) + Semgrep `p/default` | sí (Semgrep) |
| Dependencias, configuración e IaC | Trivy (`requirements.txt` con versión exacta, `Dockerfile`) | sí, en CRITICAL/HIGH con arreglo publicado |
| Imagen de producción | Trivy de la imagen; usuario sin root; sin herramientas de pruebas; Python 3.12 | sí |
| Secretos en toda la historia | gitleaks (exige ver «N commits scanned») | sí |
| Datos de terceros | revisión de confidencialidad (`herramientas/confidencialidad.py`) | sí |
| Inventario de componentes | SBOM (syft, SPDX) | no: se publica con cada versión |

Cada revisión debe probar que analizó algo (archivos, paquetes o commits > 0); un reporte vacío cuenta como
fallo.

## Excepciones y falsos positivos

- **Dependencias e imagen:** una CVE sin arreglo o no alcanzable se exceptúa en `.trivyignore`, una por línea,
  con fecha de vencimiento (`exp:AAAA-MM-DD`) y motivo; al vencer, el CI vuelve a bloquear. La propone quien
  abre el PR y la aprueba el dueño del repositorio (CODEOWNERS) al revisarlo. Hoy: ninguna.
- **Análisis estático:** un falso positivo se suprime en la línea con `# nosemgrep: <regla>` y su motivo en
  el comentario de arriba (hoy tres: un `subprocess` con lista de argumentos sin shell, `torch.save` de un
  tensor propio y un aviso de compatibilidad con Python 3.6). El SARIF que se sube omite lo suprimido.
- **Secretos:** un hallazgo de gitleaks no se suprime: el valor se reemplaza (variable de entorno o valor
  generado) y, si llegó a publicarse, se rota.

## Controles del propio sistema

- Con `API_TOKEN`, toda la API salvo `/api/v1/health` exige `X-API-Token`; cada tenant tiene su API key
  (48 hex, `secrets.token_hex`) y sólo ve y modifica sus datos (reglas T1–T11 de `docs/reglas.md`). El alta
  de tenants exige `ADMIN_TOKEN`. Las credenciales globales se comparan en tiempo constante.
- Las respuestas se apoyan sólo en la base de conocimiento del tenant y el QualityAgent revisa el riesgo de
  invención; si todos los proveedores de LLM fallan, el cliente recibe un mensaje controlado, nunca un error.
- La subida de voces valida el nombre (`[a-z0-9_-]{2,32}`), el tamaño (≤ 15 MB) y la extensión antes de
  llamar a ffmpeg con una lista de argumentos (sin shell).
- Todos los datos del repositorio son sintéticos (conversaciones de ejemplo, tenants de prueba).

## Riesgos conocidos

- Las API keys de los tenants se guardan en claro en la base (no con hash) y se comparan en la consulta.
- El webchat recibe la credencial en la URL (`?token=`): puede quedar en bitácoras de proxies o del servidor.
- No hay límite de peticiones por cliente ni CORS configurado (la API no lo necesita para el webchat propio).
- En Render (plan free) la base es SQLite efímera: no guarda datos personales entre despliegues, pero
  tampoco conserva la trazabilidad.
