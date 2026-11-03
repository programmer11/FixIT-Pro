"""Web UI for the home repair matcher. Run: python3 -m uvicorn app:app --port 8000"""
import json, os, secrets, shutil
from datetime import datetime
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import orchestrator, rag

app = FastAPI(title="FixIT-Pro")
static = rag.folder / "static"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    location: str = Field(default="", max_length=80)


def full_question(body):
    return f"{body.question} in {body.location}" if body.location.strip() else body.question


def require_admin(token):
    expected = os.getenv("ADMIN_PASSWORD")
    if not expected:
        raise HTTPException(503, "Admin is disabled. Set ADMIN_PASSWORD in .env and restart.")
    if not token or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "Incorrect admin password.")


@app.post("/api/search")
def search(body: Ask):
    return {"matches": [{k: m[k] for k in ("id", "score", "title", "fields")}
                        for m in rag.search(full_question(body))]}


@app.post("/api/answer")
def answer(body: Ask):
    question = full_question(body)
    matches = rag.search(question)
    try:
        return {"answer": rag.answer(question, matches), "sources": [m["title"] for m in matches]}
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))


@app.post("/api/admin/login")
def admin_login(x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    return {"ok": True, "local_profiles": len(rag.load_local_profiles())}


@app.post("/api/admin/upload")
async def admin_upload(file: UploadFile = File(...), x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 5 MB.")
    try:
        profiles = json.loads(raw)
        count = rag.upsert_profiles(profiles)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Not valid JSON: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Keep the local copy in sync with the index, backing up the previous file.
    if rag.profiles_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(rag.profiles_path, rag.profiles_path.with_suffix(f".{stamp}.bak.json"))
    rag.profiles_path.write_bytes(raw)
    return {"uploaded": count, "filename": file.filename}


class NewJob(BaseModel):
    request: str = Field(min_length=5, max_length=1000)
    location: str = Field(default="Santa Clara", max_length=80)
    tenant_name: str = Field(default="", max_length=80)


class Resume(BaseModel):
    step: str
    value: dict


def job_or_404(job_id, role):
    try:
        return orchestrator.job_view(job_id, role)
    except KeyError:
        raise HTTPException(404, "No such job.")


def resume_or_400(job_id, step, value, role):
    try:
        orchestrator.resume(job_id, step, value, role)
    except KeyError:
        raise HTTPException(404, "No such job.")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return orchestrator.job_view(job_id, role)


@app.post("/api/jobs")
def create_job(body: NewJob):
    return {"id": orchestrator.start_job(body.request.strip(), body.location.strip() or "Santa Clara", body.tenant_name.strip())}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return job_or_404(job_id, "tenant")


@app.post("/api/jobs/{job_id}/resume")
def tenant_resume(job_id: str, body: Resume):
    return resume_or_400(job_id, body.step, body.value, "tenant")


@app.get("/api/admin/jobs")
def admin_jobs(x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    return {"jobs": orchestrator.list_jobs()}


@app.get("/api/admin/jobs/{job_id}")
def admin_job(job_id: str, x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    return job_or_404(job_id, "owner")


@app.post("/api/admin/jobs/{job_id}/resume")
def owner_resume(job_id: str, body: Resume, x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    return resume_or_400(job_id, body.step, body.value, "owner")


@app.post("/api/admin/jobs/{job_id}/retry")
def owner_retry(job_id: str, x_admin_token: str = Header(default="")):
    require_admin(x_admin_token)
    try:
        orchestrator.retry(job_id)
    except KeyError:
        raise HTTPException(404, "No such job.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return orchestrator.job_view(job_id, "owner")


@app.get("/")
def home():
    return FileResponse(static / "index.html")


@app.get("/admin")
def admin():
    return FileResponse(static / "admin.html")


@app.get("/request")
def request_page():
    return FileResponse(static / "request.html")


@app.get("/admin/jobs")
def jobs_page():
    return FileResponse(static / "jobs.html")


app.mount("/static", StaticFiles(directory=static), name="static")
