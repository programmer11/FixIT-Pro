# API reference

All endpoints accept and return JSON unless noted. Errors return `{"detail": "..."}` with the HTTP status shown. FastAPI also serves interactive docs for these endpoints at http://localhost:8000/docs.

## `POST /api/search`

Returns the 3 profiles that best match a request. Fast; does not call the language model.

**Body**

| Field | Type | Rules |
| --- | --- | --- |
| `question` | string | Required, 3–500 characters |
| `location` | string | Optional, up to 80 characters. Appended to the question as "in &lt;location&gt;" |

```bash
curl -X POST http://localhost:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "clogged shower drain", "location": "Santa Clara"}'
```

**Response `200`**

```json
{
  "matches": [
    {
      "id": "technician-005",
      "score": 0.224,
      "title": "Real Handy Handyman",
      "fields": {"ID": "005", "Name / company": "Real Handy Handyman", "Services provided": "...", "...": "..."}
    }
  ]
}
```

`matches` is ordered best first and can be empty. `fields` contains the profile's non-empty fields.

**Errors:** `422` if the body breaks the rules above.

## `POST /api/answer`

Runs the same search, then asks the local Qwen model for a recommendation. Slow: up to a minute or more on CPU, longer for the first call after startup. Requests are handled one at a time.

**Body:** same as `/api/search`.

**Response `200`**

```json
{
  "answer": "[1] AG Handyman Services\nWhy they match: ...",
  "sources": ["AG Handyman Services / AG Custom Home Remodel and Repairs", "RC HANDYMAN SERVICES", "Real Handy Handyman"]
}
```

`sources[0]` corresponds to `[1]` in the answer, and so on. If nothing matches, `answer` says so and `sources` is empty.

**Errors:** `422` invalid body; `503` if the model file can't be found (set `QWEN_MODEL_PATH`).

## `POST /api/admin/login`

Checks the admin password.

**Headers:** `X-Admin-Token: <ADMIN_PASSWORD>`

**Response `200`:** `{"ok": true, "local_profiles": 36}`, where `local_profiles` is the number of profiles in the local file (0 if there is none).

**Errors:** `401` wrong password; `503` admin is disabled because `ADMIN_PASSWORD` isn't set.

## `POST /api/admin/upload`

Uploads a profiles file to Pinecone and saves it locally. See the [admin guide](admin-guide.md) for exactly what happens.

**Headers:** `X-Admin-Token: <ADMIN_PASSWORD>`

**Body:** `multipart/form-data` with a `file` field containing the JSON file.

```bash
curl -X POST http://localhost:8000/api/admin/upload \
  -H 'X-Admin-Token: your-password' \
  -F file=@santa_clara_home_repair_profiles.json
```

**Response `200`:** `{"uploaded": 36, "filename": "santa_clara_home_repair_profiles.json"}`

**Errors**

| Status | Cause |
| --- | --- |
| `400` | Not valid JSON, or a validation rule failed. The message names the item, e.g. "Item 3 is missing: Name / company." |
| `401` | Wrong password |
| `413` | File larger than 5 MB |
| `503` | Admin is disabled |

## Repair request workflow

These endpoints run the [repair request workflow](workflow.md). Creating or resuming a job returns at once; the agents run in the background. Poll the job to see progress.

### Job object

`GET` on a job returns:

| Field | Meaning |
| --- | --- |
| `id` | Job ID |
| `status` | `working`, `waiting`, `error`, `stalled` or `confirmed` (see [statuses](workflow.md#how-a-job-runs)) |
| `step` | Graph node that is running or waiting, e.g. `owner_approval` |
| `agent_index`, `agents` | Position in the seven-agent progress bar, and the agent names |
| `pending` | When waiting: `{"step", "role", "prompt"}`, where `role` is `owner` or `tenant` |
| `error` | Error text when `status` is `error` |
| `request_text`, `service_request`, `timeslots`, `confirmed_at` | Job details |

The tenant view adds `selected_name` and hides owner details: when the owner must act, `pending.prompt` just says "Waiting for the property owner." The owner view adds the full state: `shortlist`, `web_leads`, `quote_requests`, `quotes`, `selected`, `confirmation_message`, `log` and more. See the [state table](code-reference.md#state).

### `POST /api/jobs`

Submits a tenant request and starts the workflow.

| Field | Type | Rules |
| --- | --- | --- |
| `request` | string | Required, 5–1000 characters |
| `location` | string | Optional, up to 80 characters; defaults to Santa Clara |
| `tenant_name` | string | Optional, up to 80 characters |

```bash
curl -X POST http://localhost:8000/api/jobs -H 'Content-Type: application/json' \
  -d '{"request": "The kitchen faucet keeps dripping", "location": "Santa Clara"}'
```

**Response `200`:** `{"id": "310e2993baad4ce5a1fd23ccdfc47990"}`

### `GET /api/jobs/{id}`

The tenant's view of a job. Anyone with the ID can read it, so treat the ID like a private link. **Errors:** `404` unknown job.

### `POST /api/jobs/{id}/resume`

Answers the tenant's step. Body: `{"step": "scheduling", "value": {"timeslots": ["Mon Nov 9, 9-11am", "Tue Nov 10, 1-3pm"]}}`. Give 2 or 3 non-empty slots, up to 100 characters each. Returns the updated job.

### `GET /api/admin/jobs`

**Headers:** `X-Admin-Token`. Returns `{"jobs": [...]}`, the 100 most recent jobs, newest first. Each job has `id`, `created_at`, `request_text`, `title`, `urgency`, `status`, `step`, `agent` and `needs_owner`.

### `GET /api/admin/jobs/{id}`

**Headers:** `X-Admin-Token`. The owner's full view of a job.

### `POST /api/admin/jobs/{id}/resume`

**Headers:** `X-Admin-Token`. Answers an owner step. Body: `{"step": "<step>", "value": {...}}`:

| `step` | `value` |
| --- | --- |
| `owner_approval` | `{"approved": true}`, or `{"approved": false, "reject_ids": ["technician-004"], "feedback": "..."}`. An empty or missing `reject_ids` rejects all 3 |
| `collect_quotes` | `{"quotes": {"technician-003": {"amount": 180, "notes": "parts included"}}}`. At least one quote; leave out technicians who didn't reply |
| `pricing_approval` | `{"selected_id": "technician-003"}`, or `{"reject": true}` to search again |
| `confirm_message` | `{"approved": true, "message": "optional edited text"}`, or `{"approved": false, "feedback": "what to change"}` |

Returns the updated job, usually with `status: "working"` while the next agent runs.

### `POST /api/admin/jobs/{id}/retry`

**Headers:** `X-Admin-Token`. Continues a job in `error` or `stalled` status from its last checkpoint.

### Workflow errors

| Status | Cause |
| --- | --- |
| `400` | The step isn't the one waiting (or the job is still working), or the answer is invalid, e.g. "Give 2 or 3 time slots." |
| `401` / `503` | Wrong admin password / admin disabled (admin endpoints) |
| `403` | Wrong role, e.g. the tenant endpoint answering an owner step |
| `404` | Unknown job |

## Pages

| Method and path | Returns |
| --- | --- |
| `GET /` | Landing page |
| `GET /admin` | Admin page |
| `GET /request` | Tenant repair request page; `?job=<id>` shows a job |
| `GET /admin/jobs` | Owner console |
| `GET /static/...` | Styles, scripts and pages |
