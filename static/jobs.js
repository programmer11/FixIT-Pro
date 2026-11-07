// Owner console: list service requests and complete the owner's approval steps.
const KEY = "fixit-pro-admin-token"; // shared with the admin page
const store = {
  get: () => { try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; } },
  set: (v) => { try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY); } catch {} },
};
let token = store.get(), current = null, job = null, detailTimer = null;
const owner = (url, opts = {}) => api(url, { ...opts, token });

function signedOut(message) {
  store.set(""); token = "";
  $("console").hidden = true; $("logout").hidden = true; $("login-panel").hidden = false;
  if (message) $("login-status").innerHTML = `<div class="status err">${esc(message)}</div>`;
}

async function loadJobs() {
  try {
    const { jobs } = await owner("/api/admin/jobs");
    $("login-panel").hidden = true; $("console").hidden = false; $("logout").hidden = false;
    $("jobs").innerHTML = jobs.length ? jobs.map((j) => {
      const dot = j.needs_owner ? "owner" : j.status === "waiting" ? "" : j.status;
      const label = j.needs_owner ? `Needs you: ${j.agent}` : j.status === "waiting" ? `Waiting for tenant: ${j.agent}`
        : j.status === "confirmed" ? "Confirmed" : j.status === "working" ? `${j.agent} agent working` : `${j.agent}: ${j.status}`;
      return `<button class="jobitem${j.id === current ? " sel" : ""}" data-id="${esc(j.id)}">
        <strong>${esc(j.title || j.request_text)}</strong>
        <span class="meta"><span class="dot ${esc(dot)}"></span>${esc(label)} · ${esc(j.created_at.replace("T", " "))}${j.media_count ? ` · 📎 ${esc(j.media_count)}` : ""}</span></button>`;
    }).join("") : `<p class="meta">No requests yet. Tenants submit them at <a href="/request">/request</a>.</p>`;
    document.querySelectorAll(".jobitem").forEach((b) => b.addEventListener("click", () => openJob(b.dataset.id)));
  } catch (e) {
    signedOut(token ? e.message : "");
  }
}

async function openJob(id) {
  current = id;
  document.querySelectorAll(".jobitem").forEach((b) => b.classList.toggle("sel", b.dataset.id === id));
  try { render(await owner(`/api/admin/jobs/${encodeURIComponent(id)}`)); }
  catch (e) { $("detail").innerHTML = `<div class="status err">${esc(e.message)}</div>`; }
}

function pct(v) { return `<div class="bar"><i style="width:${Math.round((v || 0) * 100)}%"></i></div>`; }

function websiteLink(w) {
  if (!w || !isUrl(w.url)) return `<span class="empty">No website found</span>`;
  return `<a href="${esc(w.url)}" target="_blank" rel="noopener noreferrer">${esc(w.label)} ↗</a>` +
    (w.source === "web search" ? ` <span class="meta">(found by internet search — check it's the right business)</span>` : "");
}

function techCard(c, extra = "") {
  const f = c.fields || {}, b = c.breakdown || {};
  return `<div class="tech">
    <div class="tech-head"><strong>${esc(c.name)}</strong><span class="score">Score ${esc(c.score.toFixed(2))}</span></div>
    <div class="meta">${c.source === "web" ? '<span class="badge warn">Found on the internet · not verified</span> '
      : `<span class="badge">${esc(f["Operator type"] || "Technician")} · ${esc(f["Source platform"] || "database")}</span> `}</div>
    <dl class="kv" style="margin-top:10px">
      <dt>Business / technician</dt><dd><strong>${esc(c.name)}</strong></dd>
      <dt>Website</dt><dd>${websiteLink(c.website)}</dd>
    </dl>
    <div class="bars">
      <div>Job fit${pct(b.relevance)}</div><div>Licensed${pct(b.license)}</div>
      <div>Reviews${pct(b.reviews)}</div><div>Low cost${pct(b.cost)}</div>
    </div>
    <div class="reasons">${esc(c.reasons.join(" · "))}</div>
    <dl class="kv" style="margin-top:10px">
      <dt>Services</dt><dd>${esc(f["Services provided"] || "Not published")}</dd>
      <dt>Service area</dt><dd>${esc(f["Service areas / location"] || "Not published")}</dd>
      <dt>License</dt><dd>${esc(f["License evidence"] || "Not published")}</dd>
      <dt>Fee</dt><dd>${esc(f["Service fee / minimum"] || "Not published")}</dd>
      <dt>Contact</dt><dd>${esc([f.Phone, f.Email].filter(Boolean).join(" · ") || "Not published")}</dd>
    </dl>${extra}</div>`;
}

function contactLinks(r, subject, message) {
  const links = [];
  if (r.email) links.push(`<a class="btn" href="mailto:${esc(r.email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(message)}">Email</a>`);
  if (r.phone) links.push(`<a class="btn" href="tel:${esc(r.phone.replace(/[^\d+]/g, ""))}">Call ${esc(r.phone)}</a>`);
  if (isUrl(r.website)) links.push(`<a class="btn" href="${esc(r.website)}" target="_blank" rel="noopener noreferrer">Website ↗</a>`);
  return links.length ? `<div class="actions">${links.join("")}</div>` : `<p class="meta">No email, phone or website published.</p>`;
}

const STEP_VIEWS = {
  owner_approval(j) {
    const leads = j.web_leads?.length ? `<h3>More from the internet search</h3><ul class="leads">${j.web_leads.map((l) =>
      `<li>${l.business ? `<strong>${esc(l.title)}</strong> · ` : ""}<a href="${esc(l.link)}" target="_blank" rel="noopener noreferrer">${esc(l.business ? new URL(l.link).hostname.replace(/^www\./, "") : l.title)} ↗</a>
        <br><span class="meta">${esc(l.snippet)}</span></li>`).join("")}</ul>` : "";
    return `<h2>Approve the top technicians</h2>
      <p>Search round ${esc(j.search_round)}. Technicians from the database and the internet, ranked on job fit, licensing, reviews and cost.</p>
      ${j.shortlist.length ? j.shortlist.map((c) => techCard(c,
        `<label class="check"><input type="checkbox" class="rej" value="${esc(c.id)}"> Reject this technician</label>`)).join("")
        : `<p class="empty">No matching technicians found.</p>`}
      ${leads}
      <label class="field"><span>Feedback <span class="meta">(optional)</span></span><input type="text" id="fb" maxlength="500"></label>
      <div class="actions"><button class="btn primary" id="approve">Approve these technicians</button>
        <button class="btn danger" id="reject">Reject checked (or all) and search again</button></div>`;
  },
  collect_quotes(j) {
    const subject = `Quote request: ${j.service_request.title}`;
    return `<h2>Get pricing quotes</h2>
      <p>Send each technician the drafted quote request, then enter the prices they quote. Leave a price blank if they didn't reply.</p>
      ${j.media?.length ? `<p>Attach the tenant's photos and videos when you email (download them below):</p>${gallery(j.media)}` : ""}
      ${j.quote_requests.map((r) => `<div class="tech"><div class="tech-head"><strong>${esc(r.name)}</strong></div>
        <div class="msg">${esc(r.message)}</div>${contactLinks(r, subject, r.message)}
        <div class="quote-row"><input type="number" min="0" step="0.01" placeholder="Quote ($)" class="amt" data-id="${esc(r.tech_id)}">
          <input type="text" maxlength="300" placeholder="Notes, e.g. parts included" class="notes" data-id="${esc(r.tech_id)}"></div></div>`).join("")}
      <div class="actions"><button class="btn primary" id="save-quotes">Save quotes</button></div>`;
  },
  pricing_approval(j) {
    const byId = Object.fromEntries(j.shortlist.map((c) => [c.id, c]));
    const rows = Object.entries(j.quotes).sort((a, b) => a[1].amount - b[1].amount);
    return `<h2>Choose the final technician</h2>
      ${rows.map(([id, q], i) => `<label class="tech check" style="display:flex">
        <input type="radio" name="pick" value="${esc(id)}"${i ? "" : " checked"}>
        <span><strong>${esc(byId[id]?.name || id)}</strong> — $${esc(q.amount.toFixed(2))}${q.notes ? ` (${esc(q.notes)})` : ""}
        <span class="meta"> · score ${esc(byId[id]?.score.toFixed(2))}${i ? "" : " · lowest quote"}</span></span></label>`).join("")}
      <div class="actions"><button class="btn primary" id="pick">Select technician</button>
        <button class="btn danger" id="reject-all">Reject all and search again</button></div>`;
  },
  scheduling(j) {
    return `<h2>Waiting for the tenant</h2><p>Selected: <strong>${esc(j.selected.name)}</strong> at ${esc(j.selected.quote_text)}.
      The tenant has been asked for 2–3 time slots on their <a href="/request?job=${esc(j.id)}">request page</a>. Share that link with them if needed.</p>`;
  },
  confirm_message(j) {
    return `<h2>Approve the scheduling message</h2>
      <p>To <strong>${esc(j.selected.name)}</strong>. Edit it directly if you like, then approve.</p>
      <textarea class="msg" id="msg">${esc(j.confirmation_message)}</textarea>
      <div class="actions"><button class="btn primary" id="ok-msg">Approve message</button></div>
      <label class="field"><span>Or ask the agent for changes</span><input type="text" id="fb" maxlength="500" placeholder="e.g. Mention parking is on the street"></label>
      <div class="actions"><button class="btn" id="redo">Request changes</button></div>`;
  },
};

function render(j) {
  job = j;
  clearTimeout(detailTimer);
  let action = "";
  if (j.status === "waiting" && STEP_VIEWS[j.step]) action = `<div class="box">${STEP_VIEWS[j.step](j)}<div id="act-status"></div></div>`;
  if (j.status === "error" || j.status === "stalled") action = `<div class="box"><button class="btn primary" id="retry">Retry from last step</button><div id="act-status"></div></div>`;
  if (j.status === "confirmed") action = `<div class="box"><h2>Ready to send</h2><p>Send this to ${esc(j.selected.name)} to schedule the visit.</p>
    <div class="msg">${esc(j.confirmation_message)}</div>${contactLinks(j.selected, `Repair visit: ${j.service_request.title}`, j.confirmation_message)}</div>`;
  keepGalleries($("detail"), () => $("detail").innerHTML = `${stepper(j)}${statusLine(j)}${action}
    <div class="box"><h2>Service request</h2>${serviceRequest(j.service_request)}
      <p class="meta" style="margin-top:12px">Tenant wrote: “${esc(j.request_text)}”${j.tenant_name ? ` — ${esc(j.tenant_name)}` : ""}
        · <a href="/request?job=${esc(j.id)}" target="_blank" rel="noopener">Tenant's page ↗</a></p>
      ${j.media?.length ? `<h3>Photos and videos from the tenant</h3>${gallery(j.media)}` : ""}
      ${j.timeslots ? `<h3>Tenant time slots</h3><ul>${j.timeslots.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div>
    <div class="box"><h2>Agent activity</h2><ul class="timeline">${(j.log || []).slice().reverse().map((e) =>
      `<li><b>${esc(e.agent)}</b> · <span class="meta">${esc(e.at.replace("T", " "))}</span><br>${esc(e.text)}</li>`).join("")}</ul></div>`);
  bindActions();
  if (j.status === "working" || (j.status === "waiting" && j.pending?.role === "tenant"))
    detailTimer = setTimeout(() => current === j.id && openJob(j.id), j.status === "working" ? 2000 : 10000);
}

async function send(step, value) {
  document.querySelectorAll("#detail button").forEach((b) => (b.disabled = true));
  try {
    render(await owner(`/api/admin/jobs/${encodeURIComponent(job.id)}/resume`, { method: "POST", body: { step, value } }));
    loadJobs();
  } catch (e) {
    $("act-status").innerHTML = `<div class="status err">${esc(e.message)}</div>`;
    document.querySelectorAll("#detail button").forEach((b) => (b.disabled = false));
  }
}

function on(id, fn) { const el = $(id); if (el) el.addEventListener("click", fn); }

function bindActions() {
  const fb = () => $("fb")?.value.trim() || "";
  on("approve", () => send("owner_approval", { approved: true, feedback: fb() }));
  on("reject", () => send("owner_approval", { approved: false, feedback: fb(),
    reject_ids: [...document.querySelectorAll(".rej:checked")].map((c) => c.value) }));
  on("save-quotes", () => {
    const quotes = {};
    document.querySelectorAll(".amt").forEach((a) => {
      if (a.value !== "") quotes[a.dataset.id] = { amount: parseFloat(a.value),
        notes: document.querySelector(`.notes[data-id="${CSS.escape(a.dataset.id)}"]`).value.trim() };
    });
    send("collect_quotes", { quotes });
  });
  on("pick", () => send("pricing_approval", { selected_id: document.querySelector("input[name=pick]:checked")?.value }));
  on("reject-all", () => send("pricing_approval", { reject: true }));
  on("ok-msg", () => send("confirm_message", { approved: true, message: $("msg").value }));
  on("redo", () => send("confirm_message", { approved: false, feedback: fb() }));
  on("retry", async () => {
    try { render(await owner(`/api/admin/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" })); }
    catch (e) { $("act-status").innerHTML = `<div class="status err">${esc(e.message)}</div>`; }
  });
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  token = $("pw").value;
  try { await owner("/api/admin/login", { method: "POST" }); store.set(token); $("login-status").innerHTML = ""; loadJobs(); }
  catch (err) { signedOut(err.message); }
});
$("logout").addEventListener("click", (e) => { e.preventDefault(); signedOut(); });

if (token) loadJobs(); else signedOut();
setInterval(() => token && loadJobs(), 15000);
