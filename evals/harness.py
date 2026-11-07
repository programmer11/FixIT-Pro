"""Eval harness: isolation, LangSmith clients and the per-case context used by evals/cases.py.

Every eval case runs as one LangSmith trace (a root "chain" run). Calls the case makes to the API are child runs
named "HTTP <method> <route>"; the app's own instrumentation adds the LLM (Qwen), retriever (Pinecone) and tool
(web search, ranking, uploads) runs beneath them, so each trace shows inputs, outputs, latency, tokens and errors.

configure() must run before any app module (rag, agents, orchestrator, app) is imported."""
import contextlib, json, os, re, shutil, threading, time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CATALOG = json.loads((Path(__file__).parent / "catalog.json").read_text())
STEP_ORDER = ["owner_approval", "collect_quotes", "pricing_approval", "scheduling", "confirm_message", "confirmed"]
EVAL_NAMESPACE = "fixit-pro-evals"  # Pinecone namespace with the sample profiles; never the app's own namespace


def configure(work_dir):
    """Isolate the run: its own workflow DB and uploads folder, tracing on. Call before importing app modules."""
    work_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["WORKFLOW_DB"] = str(work_dir / "workflow.db")
    os.environ["UPLOADS_DIR"] = str(work_dir / "uploads")


# ---------- LangSmith clients ----------

def capture_client(path):
    """A LangSmith client that writes every run and feedback payload to a JSONL file instead of the API.
    Used by --offline, so traces can be produced and verified without a LangSmith account."""
    import requests
    from langsmith import Client

    lock = threading.Lock()

    class CaptureSession(requests.Session):
        def request(self, method, url, *args, **kwargs):
            body = kwargs.get("data") if kwargs.get("data") is not None else kwargs.get("json")
            if isinstance(body, (bytes, str)):
                try:
                    body = json.loads(body)
                except ValueError:
                    body = None
            with lock, open(path, "a") as f:
                f.write(json.dumps({"method": method, "path": urlparse(url).path, "body": body}, default=str) + "\n")
            response = requests.Response()
            response.status_code, response._content = 200, b"{}"
            response.headers["Content-Type"] = "application/json"
            return response

    return Client(api_key="offline", api_url="http://langsmith.offline", session=CaptureSession(),
                  auto_batch_tracing=False)


# ---------- test files ----------

def jpeg(size=2048):
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * max(0, size - 11)


def mov(size=4096):
    return b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00" + b"\x00" * max(0, size - 16)


def mp4(size=4096):
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * max(0, size - 16)


def m4a(size=4096):
    return b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * max(0, size - 16)


# ---------- per-case context ----------

class ApiError(Exception):
    """Raised inside a traced HTTP span for 5xx responses, so LangSmith records the span as an error."""

    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}: {response.text[:300]}")
        self.response = response


@dataclass
class Spec:
    id: str
    fn: object
    needs: tuple = ()      # child runs the trace must contain: "run_type", "run_type:name" or "...*N" for at least N
    errors: tuple = ()     # child runs that must be recorded with an error (same selector syntax)
    stage: int = 0         # execution order for cases sharing the walker job (see Ctx.walker); one step per stage


CASES = {}


def case(case_id, needs=(), errors=(), stage=0):
    def register(fn):
        CASES[case_id] = Spec(case_id, fn, tuple(needs), tuple(errors), stage)
        return fn
    return register


@dataclass
class Shared:
    """State shared by all cases in one eval run."""
    http: object
    profiles: list
    scratch_ns: str
    empty_ns: str
    walker: dict = field(default_factory=dict)  # {"id": job id} of the job walked step by step by stage>0 cases


class Ctx:
    def __init__(self, shared):
        import agents, app, media, orchestrator, rag
        self.agents, self.app, self.media, self.orchestrator, self.rag = agents, app, media, orchestrator, rag
        self.shared, self.http, self.checks, self.jobs = shared, shared.http, [], []
        self.admin = {"X-Admin-Token": os.getenv("ADMIN_PASSWORD") or ""}

    # ----- checks -----
    def check(self, name, ok, actual=None):
        actual = actual if isinstance(actual, (int, float, bool, type(None))) else str(actual)[:400]
        self.checks.append({"name": name, "passed": bool(ok), "actual": actual})
        return bool(ok)

    # ----- traced helpers -----
    def api(self, method, path, **kw):
        """One HTTP call to the app, traced as a child run with the request, status and response body."""
        from langsmith import traceable
        route = re.sub(r"[0-9a-f]{32}", "{id}", path.split("?")[0])
        files = kw.get("files") or []
        files = files.items() if isinstance(files, dict) else files
        request = {"method": method, "path": path, "json": kw.get("json"), "form": kw.get("data"),
                   "files": [{"field": k, "name": v[0], "bytes": len(v[1])} for k, v in files],
                   "headers": {k: ("***" if k.lower() == "x-admin-token" else v) for k, v in (kw.get("headers") or {}).items()}}
        if isinstance(kw.get("content"), (bytes, str)):
            request["body_bytes"] = len(kw["content"])

        holder = {}

        @traceable(run_type="chain", name=f"HTTP {method} {route}")
        def call(request):
            response = holder["response"] = self.http.request(method, path, **kw)
            if response.status_code >= 500:
                raise ApiError(response)
            return {"status": response.status_code, "body": _body(response)}

        try:
            call(request)
        except ApiError:
            pass
        return holder["response"]

    def call(self, fn, *args, name=None, **kwargs):
        """A direct call to an app function, traced as a child run (for unit-style cases)."""
        from langsmith import traceable
        return traceable(run_type="chain", name=name or f"{fn.__module__}.{fn.__name__}")(fn)(*args, **kwargs)

    # ----- workflow helpers -----
    def job(self, request, files=(), location="Santa Clara", tenant_name="Eval tenant", wait=True):
        r = self.api("POST", "/api/jobs", data={"request": request, "location": location, "tenant_name": tenant_name},
                     files=[("files", f) for f in files])
        if r.status_code != 200:
            return r, None
        job_id = r.json()["id"]
        self.jobs.append(job_id)
        return r, (self.wait(job_id) if wait else None)

    def wait(self, job_id, timeout=1500):
        """Wait until the job's background agents stop (waiting, error, stalled or confirmed); return the owner view."""
        start = time.time()
        while job_id in self.orchestrator._active:
            if time.time() - start > timeout:
                raise TimeoutError(f"Job {job_id} still working after {timeout}s")
            time.sleep(0.5)
        return self.orchestrator.job_view(job_id, "owner")

    def owner(self, job_id, step, value):
        r = self.api("POST", f"/api/admin/jobs/{job_id}/resume", json={"step": step, "value": value}, headers=self.admin)
        return r, self.wait(job_id)

    def tenant(self, job_id, step, value):
        r = self.api("POST", f"/api/jobs/{job_id}/resume", json={"step": step, "value": value})
        return r, self.wait(job_id)

    def answer_default(self, job_id, view):
        """Move a waiting job one step forward with a typical answer."""
        step = view["pending"]["step"]
        ids = [c["id"] for c in view["shortlist"] or []]
        if step == "owner_approval":
            return self.owner(job_id, step, {"approved": True})
        if step == "collect_quotes":
            quotes = {r["tech_id"]: {"amount": 180 + 60 * i, "notes": "parts included" if i == 0 else ""}
                      for i, r in enumerate(view["quote_requests"][:2])}
            return self.owner(job_id, step, {"quotes": quotes})
        if step == "pricing_approval":
            return self.owner(job_id, step, {"selected_id": next(i for i in ids if i in view["quotes"])})
        if step == "scheduling":
            return self.tenant(job_id, step, {"timeslots": ["Mon Nov 9, 9-11am", "Tue Nov 10, 1-3pm"]})
        if step == "confirm_message":
            return self.owner(job_id, step, {"approved": True})
        raise ValueError(step)

    def drive(self, job_id, to):
        """Answer steps with typical values until the job waits at `to` (or is confirmed)."""
        view = self.wait(job_id)
        while True:
            at = "confirmed" if view["status"] == "confirmed" else (view["pending"] or {}).get("step")
            if at == to or at == "confirmed":
                return view
            if view["status"] != "waiting":
                raise RuntimeError(f"Job {job_id} is {view['status']} at {view['step']}: {view.get('error')}")
            r, view = self.answer_default(job_id, view)
            if r.status_code != 200:
                raise RuntimeError(f"Driving job {job_id}: {r.status_code} {r.text[:200]}")

    def walker(self, step):
        """The shared job that stage>0 cases walk through the workflow, waiting at `step`.
        Validation-only cases leave it in place; the walker is only ever moved forward."""
        w = self.shared.walker
        if w:
            view = self.wait(w["id"])
            at = "confirmed" if view["status"] == "confirmed" else (view["pending"] or {}).get("step")
            if at in STEP_ORDER and STEP_ORDER.index(at) <= STEP_ORDER.index(step):
                return w["id"], self.drive(w["id"], step)
        r, _ = self.job("The bathroom sink drain is clogged and the water drains very slowly.")
        w["id"] = r.json()["id"]
        return w["id"], self.drive(w["id"], step)

    def finish(self):
        for job_id in self.jobs:
            self.wait(job_id)

    # ----- isolation helpers -----
    @contextlib.contextmanager
    def namespace(self, ns):
        old, self.rag.namespace = self.rag.namespace, ns
        try:
            yield
        finally:
            self.rag.namespace = old

    @contextlib.contextmanager
    def patched(self, obj, attr, value):
        old = getattr(obj, attr)
        setattr(obj, attr, value)
        try:
            yield
        finally:
            setattr(obj, attr, old)

    @contextlib.contextmanager
    def env(self, **values):
        old = {k: os.environ.get(k) for k in values}
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def poll(self, fn, until, timeout=90, interval=3):
        """Call fn until until(result) is true (Pinecone writes take a few seconds to become searchable)."""
        start = time.time()
        while True:
            result = fn()
            if until(result) or time.time() - start > timeout:
                return result
            time.sleep(interval)

    def upload(self, profiles_or_bytes, name="profiles.json"):
        raw = profiles_or_bytes if isinstance(profiles_or_bytes, bytes) else json.dumps(profiles_or_bytes).encode()
        return self.api("POST", "/api/admin/upload", files={"file": (name, raw, "application/json")}, headers=self.admin)

    def profile(self, profile_id):
        return next(p for p in self.shared.profiles if str(p["ID"]) == profile_id)


def _body(response):
    if "json" in response.headers.get("content-type", ""):
        return response.json()
    return {"content_type": response.headers.get("content-type"), "bytes": len(response.content)}


def clean_dir(path):
    shutil.rmtree(path, ignore_errors=True)
