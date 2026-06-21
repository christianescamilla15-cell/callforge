"""Minimal operations dashboard over /metrics and /tickets."""

DASHBOARD_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CallForge - Dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, sans-serif; background:#0f1115; color:#e6e6e6; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8a93a3; font-size:13px; margin-bottom:20px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(170px,1fr)); gap:12px; margin-bottom:28px; }
  .card { background:#161a22; border:1px solid #232a36; border-radius:12px; padding:14px; }
  .card .label { color:#8a93a3; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .card .value { font-size:26px; font-weight:700; margin-top:6px; }
  .card .extra { color:#8a93a3; font-size:12px; margin-top:4px; }
  h2 { font-size:15px; color:#aab3c2; margin:24px 0 10px; }
  table { width:100%; border-collapse:collapse; background:#161a22; border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:10px 12px; font-size:13px; border-bottom:1px solid #232a36; }
  th { color:#8a93a3; font-weight:600; background:#13161d; }
  tr:last-child td { border-bottom:0; }
  .pill { padding:2px 9px; border-radius:99px; font-size:11px; font-weight:600; }
  .high, .urgent { background:#4a1f1f; color:#ff8a8a; }
  .medium { background:#3d3216; color:#ffce6b; }
  .low { background:#173326; color:#6bdba0; }
  .open { background:#1d2c4a; color:#8db2ff; }
  .empty { color:#8a93a3; padding:18px; text-align:center; }
</style>
</head>
<body>
<h1>CallForge</h1>
<div class="sub">Dashboard operativo - se actualiza cada 10s</div>
<div class="cards" id="cards"></div>
<h2>Uso LLM</h2>
<table><thead><tr><th>Provider</th><th>Llamadas</th><th>Tokens in</th><th>Tokens out</th><th>Costo (USD)</th></tr></thead>
<tbody id="llm"></tbody></table>
<h2>Bandeja de tickets</h2>
<table><thead><tr><th>Ticket</th><th>Prioridad</th><th>Estado</th><th>Creado</th></tr></thead>
<tbody id="tickets"></tbody></table>
<script>
const token = new URLSearchParams(location.search).get("token");
const headers = token ? { "X-API-Token": token } : {};

function card(label, value, extra) {
  return `<div class="card"><div class="label">${label}</div>` +
         `<div class="value">${value}</div>` +
         (extra ? `<div class="extra">${extra}</div>` : "") + `</div>`;
}

async function refresh() {
  try {
    const [metricsRes, ticketsRes] = await Promise.all([
      fetch("/api/v1/metrics", { headers }),
      fetch("/api/v1/tickets", { headers }),
    ]);
    if (!metricsRes.ok) throw new Error("metrics " + metricsRes.status);
    const m = await metricsRes.json();
    const tickets = ticketsRes.ok ? await ticketsRes.json() : [];

    const status = m.conversations.by_status || {};
    const rate = m.conversations.resolution_rate;
    const rating = m.feedback.avg_rating;
    const quality = m.avg_quality_score;
    const cost = (m.llm_usage || []).reduce((acc, u) => acc + u.estimated_cost_usd, 0);

    document.getElementById("cards").innerHTML =
      card("Conversaciones", m.conversations.total,
           `activas ${status.active || 0} - resueltas ${status.resolved || 0} - escaladas ${status.escalated || 0}`) +
      card("Mensajes", m.messages_total) +
      card("Escalaciones", m.escalations_total) +
      card("Resolucion", rate == null ? "-" : Math.round(rate * 100) + "%") +
      card("Calidad media", quality == null ? "-" : quality.toFixed(2)) +
      card("Rating", rating == null ? "-" : rating.toFixed(1) + " / 5",
           `${m.feedback.count} feedbacks`) +
      card("Errores agente", m.agent_errors_total) +
      card("Costo LLM", "$" + cost.toFixed(4));

    document.getElementById("llm").innerHTML = (m.llm_usage || []).map(u =>
      `<tr><td>${u.provider}</td><td>${u.calls}</td><td>${u.tokens_in}</td>` +
      `<td>${u.tokens_out}</td><td>$${u.estimated_cost_usd.toFixed(6)}</td></tr>`
    ).join("") || `<tr><td colspan="5" class="empty">Sin uso registrado</td></tr>`;

    document.getElementById("tickets").innerHTML = tickets.map(t =>
      `<tr><td title="${t.description.replaceAll('"','&quot;')}">${t.title}</td>` +
      `<td><span class="pill ${t.priority}">${t.priority}</span></td>` +
      `<td><span class="pill ${t.status}">${t.status}</span></td>` +
      `<td>${new Date(t.created_at).toLocaleString()}</td></tr>`
    ).join("") || `<tr><td colspan="4" class="empty">Sin tickets</td></tr>`;
  } catch (err) {
    document.getElementById("cards").innerHTML = card("Error", "!", String(err));
  }
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""
