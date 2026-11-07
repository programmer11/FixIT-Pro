// Shared helpers for the tenant request page and the owner console.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isUrl = (v) => /^https?:\/\/\S+$/i.test((v || "").trim());

async function api(url, { method = "GET", body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Admin-Token"] = token;
  return parse(await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }));
}

async function upload(url, form) { // multipart form with files; the browser sets the content type
  return parse(await fetch(url, { method: "POST", body: form }));
}

async function parse(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `Request failed (${res.status})`);
  return data;
}

// Keep in step with media.py.
const MEDIA_LIMITS = { files: 8, image: 15, video: 100 }; // MB per file

function checkFiles(files, already = 0) {
  if (already + files.length > MEDIA_LIMITS.files) return `A request can have at most ${MEDIA_LIMITS.files} photos and videos.`;
  for (const f of files) {
    const kind = f.type.startsWith("video/") ? "video" : "image";
    if (f.size > MEDIA_LIMITS[kind] * 1024 * 1024) return `${f.name} is larger than ${MEDIA_LIMITS[kind]} MB.`;
  }
  return "";
}

const fmtSize = (b) => b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;

function gallery(items) {
  if (!items?.length) return "";
  return `<div class="gallery" data-key="${esc(items.map((m) => m.id).join(","))}">${items.map((m) => {
    const url = esc(m.url), name = esc(m.name);
    const view = m.kind === "video" ? `<video src="${url}" controls preload="metadata"></video>`
      : m.mime === "image/heic" ? `<a class="nopreview" href="${url}" download="${name}">HEIC photo<br><span class="meta">no preview — download</span></a>`
      : `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${name}" loading="lazy"></a>`;
    return `<figure>${view}<figcaption><a href="${url}" download="${name}">${name}</a> · ${esc(fmtSize(m.size))}</figcaption></figure>`;
  }).join("")}</div>`;
}

// Pages re-render while polling; put back unchanged galleries so a playing video isn't restarted.
function keepGalleries(container, render) {
  const old = new Map([...container.querySelectorAll(".gallery")].map((g) => [g.dataset.key, g]));
  render();
  container.querySelectorAll(".gallery").forEach((g) => {
    const same = old.get(g.dataset.key);
    if (same) { g.replaceWith(same); old.delete(g.dataset.key); }
  });
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
