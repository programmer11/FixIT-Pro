const $ = (id) => document.getElementById(id);
const form = $("search-form"), q = $("q"), loc = $("loc"), go = $("go");
const cards = $("cards"), summary = $("summary"), results = $("results");
let seq = 0; // ignore responses from superseded searches

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const missing = (v) => !v || /^not published$/i.test(v.trim());
const isUrl = (v) => /^https?:\/\/\S+$/i.test((v || "").trim());

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `Request failed (${res.status})`);
  return data;
}

function stars(rating) {
  const r = parseFloat(rating);
  if (isNaN(r)) return "";
  const full = Math.round(r);
  return `<span class="stars">${"★".repeat(full)}${"☆".repeat(5 - full)}</span> ${r.toFixed(1)}`;
}

function licenseBadge(evidence) {
  const e = (evidence || "").toLowerCase();
  if (missing(evidence)) return `<span class="badge">License not published</span>`;
  if (/conflict|unlicensed|not verified|unverified|unknown/.test(e)) return `<span class="badge warn">⚠ Verify license</span>`;
  if (/licens/.test(e)) return `<span class="badge ok">✓ License listed</span>`;
  return "";
}

function row(label, value) {
  return `<dt>${esc(label)}</dt><dd>${missing(value) ? '<span class="empty">Not published</span>' : esc(value)}</dd>`;
}

function card(m) {
  const f = m.fields, name = f["Name / company"] || m.title;
  const initials = name.split(/\s+/).filter((w) => /^[A-Za-z]/.test(w)).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  const reviews = missing(f["Review count"]) ? "" : ` · ${esc(f["Review count"])} reviews`;
  const free = /^yes/i.test(f["Free estimate / consultation"] || "");
  const website = [f["Additional source"], f["Website"]].find(isUrl);
  const phone = missing(f.Phone) ? "" : f.Phone.match(/[\d()+][\d()\-.\s]{6,}\d/)?.[0];
  const email = missing(f.Email) ? "" : f.Email.match(/[^\s;,]+@[^\s;,]+\.[a-z]{2,}/i)?.[0];
  return `<article class="card">
    <div class="card-top">
      <div class="avatar">${esc(initials || "?")}</div>
      <div>
        <h3>${esc(name)}</h3>
        <div class="meta">${stars(f["Rating / 5"])}${reviews}${f["Operator type"] ? ` · ${esc(f["Operator type"])}` : ""}</div>
        <div class="meta">${esc(f["Source platform"] || "")}</div>
      </div>
    </div>
    <div class="badges">
      ${free ? '<span class="badge ok">Free estimate</span>' : ""}
      ${licenseBadge(f["License evidence"])}
      ${missing(f["Checked on"]) ? "" : `<span class="badge">Checked ${esc(f["Checked on"])}</span>`}
    </div>
    <dl>
      ${row("Services", f["Services provided"])}
      ${row("Service area", f["Service areas / location"])}
      ${row("Service fee / minimum", f["Service fee / minimum"])}
      ${row("License evidence", f["License evidence"])}
      ${row("License number", f["License number"])}
      ${row("Availability", f["Availability / emergency"])}
      ${row("Phone", f.Phone)}
      ${row("Email", f.Email)}
    </dl>
    ${missing(f["Notes / verification gaps"]) ? "" : `<div class="note">${esc(f["Notes / verification gaps"])}</div>`}
    <div class="card-actions">
      ${phone ? `<a class="btn primary" href="tel:${esc(phone.replace(/[^\d+]/g, ""))}">Call</a>` : ""}
      ${email ? `<a class="btn" href="mailto:${esc(email)}">Email</a>` : ""}
      ${website ? `<a class="btn" href="${esc(website)}" target="_blank" rel="noopener noreferrer">Website</a>` : ""}
    </div>
  </article>`;
}

// Escape first, then render **bold** from the model's markdown.
const formatAnswer = (text) => esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/^#+\s*/gm, "");

async function run(question) {
  const body = { question: question.trim(), location: loc.value.trim() };
  if (body.question.length < 3) return q.focus();
  const id = ++seq;
  results.hidden = false;
  $("results-title").textContent = `Pros for “${body.question}”`;
  $("results-sub").textContent = body.location ? `near ${body.location}` : "";
  cards.innerHTML = `<p><span class="spinner"></span>Finding matching pros…</p>`;
  summary.innerHTML = `<span class="spinner"></span>Waiting for matches…`;
  go.disabled = true;
  results.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const { matches } = await post("/api/search", body);
    if (id !== seq) return;
    cards.innerHTML = matches.length ? matches.map(card).join("")
      : `<p class="empty">No matching pros yet. Try describing the job differently.</p>`;
    if (!matches.length) { summary.textContent = "No profiles to summarize."; return; }
    summary.innerHTML = `<span class="spinner"></span>Writing a recommendation (this can take up to a minute on CPU)…`;
  } catch (e) {
    if (id === seq) { cards.innerHTML = `<div class="error">${esc(e.message)}</div>`; summary.textContent = ""; }
    return;
  } finally {
    if (id === seq) go.disabled = false;
  }

  try {
    const { answer } = await post("/api/answer", body);
    if (id === seq) summary.innerHTML = formatAnswer(answer);
  } catch (e) {
    if (id === seq) summary.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); run(q.value); });
document.querySelectorAll("[data-q]").forEach((el) =>
  el.addEventListener("click", () => { q.value = el.dataset.q; run(el.dataset.q); }));
