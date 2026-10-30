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

## Pages

| Method and path | Returns |
| --- | --- |
| `GET /` | Landing page |
| `GET /admin` | Admin page |
| `GET /static/...` | Styles, scripts and pages |
