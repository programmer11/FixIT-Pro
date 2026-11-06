"""Web UI for the home repair matcher. Run: python3 -m uvicorn app:app --port 8000"""
import json, os, secrets, shutil, uuid
from datetime import datetime
from urllib.parse import quote
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import media, orchestrator, rag

app = FastAPI(title="FixIT-Pro")
static = rag.folder / "static"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@app.middleware("http")
async def limit_media_uploads(request: Request, call_next):
    """Reject oversized photo/video uploads before the body is read (FastAPI spools whole uploads to disk)."""
    if request.method == "POST" and request.url.path.startswith("/api/jobs") and \
            int(request.headers.get("content-length") or 0) > media.MAX_REQUEST_BYTES:
        return JSONResponse({"detail": f"Upload is larger than {media.MAX_REQUEST_BYTES // (1024 * 1024)} MB."}, 413)
    return await call_next(request)


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


async def save_media_or_400(job_id, files):
    try:
        return await media.save(job_id, files)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs")
async def create_job(request: str = Form(min_length=5, max_length=1000), location: str = Form("Santa Clara", max_length=80),
                     tenant_name: str = Form("", max_length=80), files: list[UploadFile] = File(default=[])):
    """Multipart form: the tenant's request plus optional photos/videos. Files are checked before the job starts."""
    job_id = uuid.uuid4().hex
    await save_media_or_400(job_id, files)
    return {"id": orchestrator.start_job(request.strip(), location.strip() or "Santa Clara", tenant_name.strip(), job_id=job_id)}


@app.post("/api/jobs/{job_id}/media")
async def add_media(job_id: str, files: list[UploadFile] = File(...)):
    view = job_or_404(job_id, "tenant")
    if view["status"] == "confirmed":
        raise HTTPException(400, "This request is already confirmed. Send further photos to the owner directly.")
    await save_media_or_400(job_id, files)
    return job_or_404(job_id, "tenant")


@app.get("/api/jobs/{job_id}/media/{media_id}")
def get_media(job_id: str, media_id: str):
    try:
        path, entry = media.file_of(job_id, media_id)
    except KeyError:
        raise HTTPException(404, "No such file.")
    # Served with its detected type only; sandboxed so a file can never run as a page on this site.
    return FileResponse(path, media_type=entry["mime"], headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(entry['name'])}", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=3600"})


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
