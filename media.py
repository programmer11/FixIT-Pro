"""Photos and videos a tenant attaches to a repair request.

Files live in UPLOADS_DIR/<job id>/ under generated names, with an index.json listing them. The file type is
detected from the file's first bytes, never from its name or the browser's content type, and only common photo
and video formats are accepted (no SVG or HTML, which a browser could run as a page)."""
import json, os, re, threading, uuid
from datetime import datetime
from pathlib import Path
import rag

uploads_dir = Path(os.getenv("UPLOADS_DIR") or rag.folder / "uploads")
MAX_FILES = 8                       # per job
MAX_BYTES = {"image": 15 * 1024 * 1024, "video": 100 * 1024 * 1024}
MAX_REQUEST_BYTES = 250 * 1024 * 1024  # whole upload request, checked before the body is read
CHUNK = 1024 * 1024
_lock = threading.Lock()


def sniff(head):
    """(kind, mime type, extension) for a supported photo/video, from its first bytes; None otherwise."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif", ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp", ".webp"
    if head[4:8] == b"ftyp":  # ISO media: iPhone photos (HEIC), MP4 and QuickTime videos
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
            return "image", "image/heic", ".heic"
        if brand in (b"avif", b"avis"):
            return "image", "image/avif", ".avif"
        if brand == b"qt  ":
            return "video", "video/quicktime", ".mov"
        return "video", "video/mp4", ".mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm", ".webm"
    return None


def _job_dir(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        raise KeyError(job_id)
    return uploads_dir / job_id


def list_media(job_id):
    index = _job_dir(job_id) / "index.json"
    return json.loads(index.read_text()) if index.exists() else []


def describe(items):
    """e.g. '2 photos and 1 video'."""
    counts = [(sum(m["kind"] == k for m in items), k) for k in ("image", "video")]
    words = [f"{n} {'photo' if k == 'image' else 'video'}{'s' if n > 1 else ''}" for n, k in counts if n]
    return " and ".join(words)


def _display_name(filename):
    name = re.sub(r"[\x00-\x1f]", "", Path(filename or "").name).strip()
    return name[:100] or "upload"


async def save(job_id, files):
    """Validate and store uploaded files (FastAPI UploadFiles). All or nothing: on any error, nothing is kept
    and ValueError explains why. Returns the new media entries."""
    files = [f for f in files if f.filename]  # an empty file input still sends one nameless part
    if not files:
        return []
    folder = _job_dir(job_id)
    if len(list_media(job_id)) + len(files) > MAX_FILES:
        raise ValueError(f"A request can have at most {MAX_FILES} photos and videos.")
    folder.mkdir(parents=True, exist_ok=True)
    saved, path = [], None
    try:
        for f in files:
            name = _display_name(f.filename)
            head = await f.read(64)
            found = sniff(head)
            if not found:
                raise ValueError(f"{name} isn't a supported photo or video (JPEG, PNG, GIF, WebP, HEIC, MP4, MOV or WebM).")
            kind, mime, ext = found
            media_id = uuid.uuid4().hex
            path, size = folder / f"{media_id}{ext}", len(head)
            with open(path, "wb") as out:
                out.write(head)
                while chunk := await f.read(CHUNK):
                    size += len(chunk)
                    if size > MAX_BYTES[kind]:
                        raise ValueError(f"{name} is larger than {MAX_BYTES[kind] // (1024 * 1024)} MB.")
                    out.write(chunk)
            saved.append({"id": media_id, "kind": kind, "mime": mime, "file": path.name, "name": name, "size": size,
                          "uploaded_at": datetime.now().isoformat(timespec="seconds")})
            path = None
    except BaseException:
        for p in [path] + [folder / m["file"] for m in saved]:
            if p:
                p.unlink(missing_ok=True)
        if not any(folder.iterdir()):  # a rejected new request leaves no trace
            folder.rmdir()
        raise
    with _lock:
        items = list_media(job_id) + saved
        (folder / "index.json").write_text(json.dumps(items, indent=1))
    return saved


def file_of(job_id, media_id):
    """(path, entry) of one uploaded file; KeyError if it doesn't exist."""
    entry = next((m for m in list_media(job_id) if m["id"] == media_id), None)
    if not entry:
        raise KeyError(media_id)
    return _job_dir(job_id) / entry["file"], entry
