# FixIT-Pro

A home repair technician matcher for the Santa Clara area. Describe a job in plain words ("my shower drain is clogged") and FixIT-Pro finds the best-matching technicians, showing their services, fees, license evidence and contact details side by side, plus a short AI recommendation.

It's a small retrieval-augmented generation (RAG) app:

- **Retrieval:** technician profiles are stored in a [Pinecone](https://www.pinecone.io/) index, which embeds and searches them with its integrated embedding model.
- **Generation:** a local [Qwen 2.5 3B Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF) model (GGUF, via `llama-cpp-python`) writes the recommendation, using only the retrieved profiles. No OpenAI key is needed.
- **Web UI:** a FastAPI server with a landing page, search results as provider cards, and an admin page for uploading profiles.

▶ Watch the [search demo](FixIT-Pro-demo.mp4) and the [repair workflow demo](FixIT-Pro-workflow-demo.mp4). Full documentation is in [docs/](docs/README.md), including the [workflow guide](docs/workflow.md) and [code reference](docs/code-reference.md).

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

## Repair request workflow (multi-agent)

Tenants can also submit a repair request at http://localhost:8000/request, with photos or videos of the problem. A [LangGraph](https://langchain-ai.github.io/langgraph/) orchestrator (`orchestrator.py`) moves it through seven agents (`agents.py`), pausing whenever a person must decide:

| # | Agent | What it does | Who acts |
| --- | --- | --- | --- |
| 1 | Translate | Turns the tenant's plain text into a service request (title, category, urgency, description) with Qwen. Hazard keywords such as "sparks" or "flooding" always set emergency urgency. | Agent |
| 2 | Search & Match | Searches Pinecone and the internet (LangChain's DuckDuckGo tool, Bing backend), then ranks database and internet technicians together on job fit 30%, licensing 30%, reviews 25% (rating adjusted for review count) and low cost 15%. Keeps the top 3, each with its business/technician name and website. A missing website is looked up online by name. | Agent |
| 3 | Owner Approval | Owner approves the 3, or rejects some or all. Rejected technicians are excluded and the search runs again, until the owner approves. | Owner |
| 4 | Pricing | Drafts a quote request for each technician (email/call links in the console). The owner enters the quotes they get back. | Agent + owner |
| 5 | Pricing Approval | Owner compares the quotes and picks the final technician, or rejects all and searches again. | Owner |
| 6 | Scheduling | Tenant gives 2–3 time slots on their request page. | Tenant |
| 7 | Job Confirmation | Drafts the message asking the technician to confirm a slot. Owner edits and approves it, or asks the agent for changes. | Agent + owner |

The owner works in the **owner console** at http://localhost:8000/admin/jobs (same `ADMIN_PASSWORD`). Each request's progress, agent activity log and the action for the current step are shown there.

- The app doesn't send email or call anyone. It drafts the messages and gives you email and call links, and you enter the quotes you get back.
- Tenant photos and videos are stored in `uploads/` (git-ignored), shown to the owner in the console, and mentioned in the quote requests so the owner can attach them. The local model reads text only, so the agents don't look at the images.
- Each step is saved to `workflow.db` (SQLite, git-ignored), so requests survive a server restart. If a step fails or is interrupted, use **Retry from last step** in the console.
- If the Qwen model file is missing, the agents fall back to keyword rules and message templates, so the workflow still runs.
- Businesses found on the internet are ranked only on what their search result shows: job fit is discounted, and licensing, rating and fees come from the snippet. They're marked **not verified**, and the ones outside the top 3 are listed under the shortlist. A website found by name search is flagged so the owner can check it's the right business. Individuals listed as "First L." (for example TaskRabbit) only get a link to their profile on that platform.

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
| `agents.py` | The seven workflow agents and the technician ranking |
| `orchestrator.py` | LangGraph workflow, approval checkpoints, saved job state |
| `media.py` | Tenant photo and video uploads: validation and storage |
| `static/` | Landing page, admin page, styles and browser script |
| `home_repair_rag.ipynb` | The original notebook version of the pipeline |
| `docs/` | Architecture, workflow, code reference, setup, user and admin guides, data format, API reference, troubleshooting |

## API

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/api/search` | `{"question": "...", "location": "Santa Clara"}` | Top 3 matching profiles |
| `POST` | `/api/answer` | same as above | AI recommendation and source names |
| `POST` | `/api/admin/upload` | multipart `file`, header `X-Admin-Token` | Number of profiles uploaded |
| `POST` | `/api/jobs` | multipart: `request`, `location`, `tenant_name`, optional `files` (photos/videos) | New job `id` |
| `POST` | `/api/jobs/{id}/media` | multipart `files` | Adds photos/videos to a request |
| `GET` | `/api/jobs/{id}/media/{media_id}` | | One uploaded photo or video |
| `GET` | `/api/jobs/{id}` | | Tenant's view of the job |
| `POST` | `/api/jobs/{id}/resume` | `{"step": "scheduling", "value": {"timeslots": [...]}}` | Updated job |
| `GET` | `/api/admin/jobs` | header `X-Admin-Token` | All jobs and their current step |
| `GET` | `/api/admin/jobs/{id}` | header `X-Admin-Token` | Full job state for the owner |
| `POST` | `/api/admin/jobs/{id}/resume` | `{"step": "...", "value": {...}}`, header `X-Admin-Token` | Updated job (owner steps) |
| `POST` | `/api/admin/jobs/{id}/retry` | header `X-Admin-Token` | Re-runs a failed or interrupted step |

## Notes

- Recommendations come from the profile data only, which may be incomplete or out of date. Always confirm licensing, pricing and availability directly with the provider.
- The admin password is a simple shared secret meant for local use. Add proper authentication and HTTPS before deploying publicly.
- Pinecone usage may incur charges.
