# FixIT-Pro

A home repair technician matcher for the Santa Clara area. Describe a job in plain words ("my shower drain is clogged") and FixIT-Pro finds the best-matching technicians, showing their services, fees, license evidence and contact details side by side, plus a short AI recommendation.

It's a small retrieval-augmented generation (RAG) app:

- **Retrieval:** technician profiles are stored in a [Pinecone](https://www.pinecone.io/) index, which embeds and searches them with its integrated embedding model.
- **Generation:** a local [Qwen 2.5 3B Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF) model (GGUF, via `llama-cpp-python`) writes the recommendation, using only the retrieved profiles. No OpenAI key is needed.
- **Web UI:** a FastAPI server with a landing page, search results as provider cards, and an admin page for uploading profiles.

## Requirements

- Python 3.10+
- A Pinecone account and an index with an integrated embedding model (the `chunk_text` field is embedded)
- A Qwen GGUF model file, for example `qwen2.5-3b-instruct-q4_k_m.gguf`

## Setup

```bash
git clone https://github.com/programmer11/FixIT-Pro.git
cd FixIT-Pro
pip install -r requirements.txt
```

Create a `.env` file in the project folder:

```bash
PINECONE_API_KEY=your-pinecone-key
PINECONE_INDEX_NAME=your-index-name
QWEN_MODEL_PATH=/path/to/qwen2.5-3b-instruct-q4_k_m.gguf
ADMIN_PASSWORD=choose-a-password   # enables the /admin page
```

`.env` is git-ignored. Don't commit it.

## Run

```bash
python3 -m uvicorn app:app --port 8000
```

Open http://localhost:8000. Keep the terminal open while you use the app; press Ctrl+C to stop it.

The first AI recommendation takes longer (up to a minute or two on CPU) because the model loads on first use. Provider cards appear right away while the recommendation is generated.

## Loading technician profiles

Profile data is **not included** in this repository. To add profiles:

1. Open http://localhost:8000/admin and sign in with `ADMIN_PASSWORD`.
2. Expand **Upload technician profiles (optional)** and upload a JSON file.

The file must be a JSON array of objects. Each profile needs a unique `ID` and a `Name / company`; any other fields are indexed and shown on the cards. These fields are displayed when present:

```json
[
  {
    "ID": "001",
    "Name / company": "Example Handyman LLC",
    "Operator type": "Company",
    "Source platform": "Direct website",
    "Rating / 5": "4.5",
    "Review count": "12",
    "Service areas / location": "Santa Clara, San Jose",
    "Services provided": "Plumbing fixtures; drywall; TV mounting",
    "Free estimate / consultation": "Yes",
    "Service fee / minimum": "Not published",
    "License evidence": "Not verified",
    "License number": "Not published",
    "Availability / emergency": "Contact to confirm",
    "Phone": "555-555-0100",
    "Email": "hello@example.com",
    "Additional source": "https://example.com/",
    "Notes / verification gaps": "Confirm license before contracting.",
    "Checked on": "2026-10-01"
  }
]
```

Uploading again updates profiles with the same `ID` and adds new ones. Profiles removed from the file are **not** deleted from the Pinecone index. The uploaded file is also saved locally as `santa_clara_home_repair_profiles.json` (git-ignored), with a timestamped backup of the previous version.

You can also upload profiles from the notebook: put the JSON file next to `home_repair_rag.ipynb` and run its cells top to bottom.

## Project layout

| Path | What it is |
| --- | --- |
| `app.py` | FastAPI server: pages, search, AI answer and admin upload endpoints |
| `rag.py` | Pinecone search, profile upload and Qwen answer generation |
| `static/` | Landing page, admin page, styles and browser script |
| `home_repair_rag.ipynb` | The original notebook version of the pipeline |

## API

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/api/search` | `{"question": "...", "location": "Santa Clara"}` | Top 3 matching profiles |
| `POST` | `/api/answer` | same as above | AI recommendation and source names |
| `POST` | `/api/admin/upload` | multipart `file`, header `X-Admin-Token` | Number of profiles uploaded |

## Notes

- Recommendations come from the profile data only, which may be incomplete or out of date. Always confirm licensing, pricing and availability directly with the provider.
- The admin password is a simple shared secret meant for local use. Add proper authentication and HTTPS before deploying publicly.
- Pinecone usage may incur charges.
