"""LangGraph orchestrator for the tenant-request -> confirmed-visit workflow.

translate -> search_match -> owner_approval --rejected--> search_match (rejected technicians excluded)
                                   | approved
                          pricing -> collect_quotes -> pricing_approval --reject all--> search_match
                                                             | technician selected
                          scheduling (tenant time slots) -> job_confirmation -> confirm_message --changes--> job_confirmation
                                                                                      | approved
                                                                                     END

Work agents live in agents.py. The *_gate nodes below only pause for a person (LangGraph interrupt) and route,
so resuming never repeats an LLM call. Every step is checkpointed to SQLite, so jobs survive a server restart."""
import operator, os, sqlite3, threading, uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, TypedDict
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
import agents, media, rag
from agents import log

db_path = Path(os.getenv("WORKFLOW_DB") or rag.folder / "workflow.db")  # override for demos and tests

AGENTS = ["Translate", "Search & Match", "Owner Approval", "Pricing", "Pricing Approval", "Scheduling", "Job Confirmation"]
NODE_AGENT = {"translate": 0, "search_match": 1, "owner_approval": 2, "pricing": 3, "collect_quotes": 3,
              "pricing_approval": 4, "scheduling": 5, "job_confirmation": 6, "confirm_message": 6}
PROMPTS = {
    "owner_approval": ("owner", "Approve the top 3 technicians, or reject some or all of them to search again."),
    "collect_quotes": ("owner", "Send the quote requests, then enter each technician's quoted price."),
    "pricing_approval": ("owner", "Choose the final technician from the quotes."),
    "scheduling": ("tenant", "Give 2–3 time slots when the technician can visit."),
    "confirm_message": ("owner", "Approve the scheduling message to the technician, or ask for changes."),
}


class JobState(TypedDict, total=False):
    request_text: str
    location: str
    tenant_name: str
    service_request: dict
    shortlist: list
    web_leads: list
    excluded_ids: list
    search_round: int
    quote_requests: list
    quotes: dict
    selected: dict
    timeslots: list
    confirmation_message: str
    message_feedback: str
    confirmed_at: str
    log: Annotated[list, operator.add]


def ask(step):
    role, prompt = PROMPTS[step]
    return interrupt({"step": step, "role": role, "prompt": prompt})


# ---------- 3. Owner Approval Agent ----------

def owner_approval_gate(state) -> Command[Literal["pricing", "search_match"]]:
    decision = ask("owner_approval")
    names = ", ".join(c["name"] for c in state["shortlist"])
    if decision["approved"]:
        return Command(goto="pricing", update=log("Owner Approval", f"Owner approved: {names}."))
    rejected = decision.get("reject_ids") or [c["id"] for c in state["shortlist"]]
    rejected_names = ", ".join(c["name"] for c in state["shortlist"] if c["id"] in rejected)
    note = f" Feedback: {decision['feedback']}" if decision.get("feedback") else ""
    return Command(goto="search_match", update={
        "excluded_ids": sorted(set(state.get("excluded_ids") or []) | set(rejected)),
        **log("Owner Approval", f"Owner rejected: {rejected_names}. Searching again.{note}")})


# ---------- 4. Pricing Agent (collect the quotes the technicians sent back) ----------

def collect_quotes_gate(state) -> Command[Literal["pricing_approval"]]:
    quotes = ask("collect_quotes")["quotes"]
    summary = "; ".join(f"{r['name']}: {agents.quote_text(quotes[r['tech_id']]) if r['tech_id'] in quotes else 'no quote'}"
                        for r in state["quote_requests"])
    return Command(goto="pricing_approval", update={"quotes": quotes, **log("Pricing", f"Quotes received. {summary}.")})


# ---------- 5. Pricing Approval Agent ----------

def pricing_approval_gate(state) -> Command[Literal["scheduling", "search_match"]]:
    decision = ask("pricing_approval")
    if decision.get("reject"):
        return Command(goto="search_match", update={
            "quotes": {}, "excluded_ids": sorted(set(state.get("excluded_ids") or []) | {c["id"] for c in state["shortlist"]}),
            **log("Pricing Approval", "Owner rejected all quotes. Searching for new technicians.")})
    tech = next(c for c in state["shortlist"] if c["id"] == decision["selected_id"])
    quote = state["quotes"][tech["id"]]
    selected = {"id": tech["id"], "name": tech["name"], **agents.contact_of_candidate(tech),
                "quote": quote, "quote_text": agents.quote_text(quote)}
    return Command(goto="scheduling", update={
        "selected": selected, **log("Pricing Approval", f"Owner selected {tech['name']} at {selected['quote_text']}.")})


# ---------- 6. Scheduling Agent ----------

def scheduling_gate(state) -> Command[Literal["job_confirmation"]]:
    slots = ask("scheduling")["timeslots"]
    return Command(goto="job_confirmation", update={
        "timeslots": slots, **log("Scheduling", f"Tenant offered {len(slots)} time slots: {'; '.join(slots)}.")})


# ---------- 7. Job Confirmation Agent (owner approves the drafted message) ----------

def confirm_message_gate(state) -> Command[Literal["job_confirmation", "__end__"]]:
    decision = ask("confirm_message")
    if decision["approved"]:
        message = decision.get("message") or state["confirmation_message"]
        return Command(goto=END, update={
            "confirmation_message": message, "confirmed_at": datetime.now().isoformat(timespec="seconds"),
            **log("Job Confirmation", f"Owner approved the message to {state['selected']['name']}. Ready to send.")})
    return Command(goto="job_confirmation", update={
        "message_feedback": decision["feedback"], **log("Job Confirmation", f"Owner asked for changes: {decision['feedback']}")})


def build_graph(checkpointer):
    g = StateGraph(JobState)
    g.add_node("translate", agents.translate_agent)
    g.add_node("search_match", agents.search_match_agent)
    g.add_node("owner_approval", owner_approval_gate)
    g.add_node("pricing", agents.pricing_agent)
    g.add_node("collect_quotes", collect_quotes_gate)
    g.add_node("pricing_approval", pricing_approval_gate)
    g.add_node("scheduling", scheduling_gate)
    g.add_node("job_confirmation", agents.job_confirmation_agent)
    g.add_node("confirm_message", confirm_message_gate)
    g.add_edge(START, "translate")
    g.add_edge("translate", "search_match")
    g.add_edge("search_match", "owner_approval")
    g.add_edge("pricing", "collect_quotes")
    g.add_edge("job_confirmation", "confirm_message")
    return g.compile(checkpointer=checkpointer)


graph = build_graph(SqliteSaver(sqlite3.connect(db_path, check_same_thread=False)))
_conn = sqlite3.connect(db_path, check_same_thread=False)  # jobs index; used only while holding _guard
_conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created_at TEXT, request_text TEXT)")
_active, _errors, _guard = set(), {}, threading.Lock()


def _config(job_id):
    return {"configurable": {"thread_id": job_id}}


def _run(job_id, payload):
    try:
        graph.invoke(payload, _config(job_id))
    except Exception as e:
        _errors[job_id] = f"{type(e).__name__}: {e}"
    finally:
        with _guard:
            _active.discard(job_id)


def _launch(job_id, payload):
    """Call with _guard held. Runs the graph in the background until the next human checkpoint."""
    if job_id in _active:
        raise ValueError("This job is still being processed. Try again in a moment.")
    _active.add(job_id)
    _errors.pop(job_id, None)
    threading.Thread(target=_run, args=(job_id, payload), daemon=True).start()


def _snapshot(job_id):
    snap = graph.get_state(_config(job_id))
    if not snap.values and job_id not in _active and job_id not in _errors:  # active/failed before first checkpoint
        raise KeyError(job_id)
    pending = next((i.value for t in snap.tasks for i in t.interrupts), None)
    return snap, pending


def start_job(request_text, location, tenant_name="", job_id=None):
    job_id = job_id or uuid.uuid4().hex
    with _guard:
        _conn.execute("INSERT INTO jobs VALUES (?, ?, ?)", (job_id, datetime.now().isoformat(timespec="seconds"), request_text))
        _conn.commit()
        _launch(job_id, {"request_text": request_text, "location": location, "tenant_name": tenant_name,
                         **log("Tenant", f"Request submitted: {request_text}")})
    return job_id


def validate(step, value, state):
    """Raise ValueError unless value is a valid answer to the checkpoint `step`; return the cleaned value."""
    if step == "owner_approval":
        ids = {c["id"] for c in state["shortlist"]}
        reject = [i for i in value.get("reject_ids") or [] if i in ids]
        if value.get("approved") and not ids:
            raise ValueError("There are no technicians to approve. Reject to search again.")
        return {"approved": bool(value.get("approved")), "reject_ids": reject, "feedback": str(value.get("feedback") or "")[:500]}
    if step == "collect_quotes":
        ids, quotes = {r["tech_id"] for r in state["quote_requests"]}, {}
        for tech_id, q in (value.get("quotes") or {}).items():
            if tech_id in ids and q.get("amount") not in (None, ""):
                amount = float(q["amount"])
                if not 0 <= amount < 1_000_000:
                    raise ValueError("Quote amounts must be between 0 and 1,000,000.")
                quotes[tech_id] = {"amount": amount, "notes": str(q.get("notes") or "")[:300]}
        if not quotes:
            raise ValueError("Enter at least one quote.")
        return {"quotes": quotes}
    if step == "pricing_approval":
        if value.get("reject"):
            return {"reject": True}
        if value.get("selected_id") not in state.get("quotes", {}):
            raise ValueError("Choose a technician who sent a quote.")
        return {"selected_id": value["selected_id"]}
    if step == "scheduling":
        slots = [str(s).strip()[:100] for s in value.get("timeslots") or [] if str(s).strip()]
        if not 2 <= len(slots) <= 3:
            raise ValueError("Give 2 or 3 time slots.")
        return {"timeslots": slots}
    if step == "confirm_message":
        if value.get("approved"):
            return {"approved": True, "message": str(value.get("message") or "").strip()[:4000]}
        if not str(value.get("feedback") or "").strip():
            raise ValueError("Say what should change in the message.")
        return {"approved": False, "feedback": str(value["feedback"]).strip()[:500]}
    raise ValueError(f"Unknown step {step!r}.")


def resume(job_id, step, value, role):
    with _guard:
        snap, pending = _snapshot(job_id)
        if job_id in _active or not pending or pending["step"] != step:
            raise ValueError("This step is not waiting for input. Refresh to see the job's current status.")
        if pending["role"] != role:
            raise PermissionError(f"Only the {pending['role']} can complete this step.")
        _launch(job_id, Command(resume=validate(step, value, snap.values)))


def retry(job_id):
    """Re-run a job from its last checkpoint after an error or a server restart."""
    with _guard:
        snap, pending = _snapshot(job_id)
        if job_id in _active or pending or not snap.next:
            raise ValueError("This job doesn't need a retry.")
        _launch(job_id, None)


def job_view(job_id, role):
    snap, pending = _snapshot(job_id)
    v = snap.values
    node = snap.next[0] if snap.next else ("translate" if not v else None)
    if job_id in _active:
        status, pending = "working", None  # the checkpoint's interrupt has already been answered
    elif pending:
        status, node = "waiting", pending["step"]
    elif job_id in _errors:
        status = "error"
    else:
        status = "stalled" if node else "confirmed"
    view = {"id": job_id, "status": status, "step": node, "agent_index": NODE_AGENT.get(node, len(AGENTS)),
            "agents": AGENTS, "pending": pending, "error": _errors.get(job_id),
            "request_text": v.get("request_text"), "service_request": v.get("service_request"),
            "timeslots": v.get("timeslots"), "confirmed_at": v.get("confirmed_at"),
            "media": [{**{k: m[k] for k in ("id", "kind", "mime", "name", "size", "uploaded_at")},
                       "url": f"/api/jobs/{job_id}/media/{m['id']}"} for m in media.list_media(job_id)]}
    if role == "tenant":
        view["selected_name"] = (v.get("selected") or {}).get("name")
        if pending and pending["role"] != "tenant":
            view["pending"] = {"step": pending["step"], "role": pending["role"], "prompt": "Waiting for the property owner."}
        return view
    return {**view, **{k: v.get(k) for k in ("location", "tenant_name", "shortlist", "web_leads", "excluded_ids",
                                             "search_round", "quote_requests", "quotes", "selected",
                                             "confirmation_message", "log")}}


def list_jobs():
    with _guard:
        rows = _conn.execute("SELECT id, created_at, request_text FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    jobs = []
    for job_id, created, text in rows:
        try:
            view = job_view(job_id, "owner")
        except KeyError:
            continue
        sr = view["service_request"] or {}
        jobs.append({"id": job_id, "created_at": created, "request_text": text, "title": sr.get("title"),
                     "urgency": sr.get("urgency"), "status": view["status"], "step": view["step"],
                     "agent": AGENTS[view["agent_index"]] if view["agent_index"] < len(AGENTS) else "Done",
                     "media_count": len(view["media"]),
                     "needs_owner": bool(view["pending"] and view["pending"]["role"] == "owner")})
    return jobs
