// Shared helpers for the tenant request page and the owner console.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isUrl = (v) => /^https?:\/\/\S+$/i.test((v || "").trim());

async function api(url, { method = "GET", body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Admin-Token"] = token;
  const res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `Request failed (${res.status})`);
  return data;
}

function stepper(job) {
  return `<ol class="stepper">${job.agents.map((name, i) => {
    const cls = i < job.agent_index || job.status === "confirmed" ? "done"
      : i === job.agent_index ? (job.status === "waiting" ? "wait" : "now") : "";
    return `<li class="${cls}">${esc(name)}</li>`;
  }).join("")}</ol>`;
}

function serviceRequest(sr) {
  if (!sr) return `<p><span class="spinner"></span>The Translate agent is writing up the service request…</p>`;
  return `<dl class="kv">
    <dt>Job</dt><dd><strong>${esc(sr.title)}</strong></dd>
    <dt>Category</dt><dd>${esc(sr.category)}</dd>
    <dt>Urgency</dt><dd><span class="badge urg-${esc(sr.urgency)}">${esc(sr.urgency)}</span></dd>
    <dt>Location</dt><dd>${esc(sr.location)}</dd>
    <dt>Details</dt><dd>${esc(sr.description)}</dd>
  </dl>`;
}

function statusLine(job) {
  if (job.status === "working") return `<p><span class="spinner"></span>The ${esc(job.agents[job.agent_index] || "")} agent is working…</p>`;
  if (job.status === "confirmed") return `<div class="status ok">Visit scheduling message approved on ${esc(job.confirmed_at?.replace("T", " "))}.</div>`;
  if (job.status === "error") return `<div class="status err">This step failed: ${esc(job.error)}</div>`;
  if (job.status === "stalled") return `<div class="status err">This job was interrupted (for example by a server restart).</div>`;
  return job.pending ? `<div class="status info">${esc(job.pending.prompt)}</div>` : "";
}
