# Code reference

Where the code lives and how to change it. For the concepts behind it, read [Architecture](architecture.md) and [Repair request workflow](workflow.md) first.

## Files

| File | Responsibility |
| --- | --- |
| `app.py` | FastAPI server: pages, search, admin upload and the job endpoints. Thin: validation and HTTP errors only |
| `rag.py` | Pinecone connection, profile upload, search, and `chat()` for the shared local Qwen model |
| `agents.py` | The work agents, the technician ranking, internet search and website matching |
| `orchestrator.py` | The LangGraph graph, the human gates, checkpointing, the background runner and the job views |
| `media.py` | Tenant photo and video uploads: type detection, size limits, storage and lookup |
| `static/request.html` | Tenant page: submit a request, follow progress, enter time slots |
| `static/jobs.html`, `static/jobs.js` | Owner console: job list and one view per waiting step |
| `static/workflow.js`, `static/workflow.css` | Helpers and styles shared by the two workflow pages |
| `static/index.html`, `static/app.js`, `static/admin.html`, `static/styles.css` | The original search and admin pages |

## `rag.py`

| Name | What it does |
| --- | --- |
| `search(question, top_k=3)` | Pinecone semantic search; returns `id`, `score`, `title`, `chunk_text` and parsed `fields` per match |
| `upsert_profiles(profiles)` | Validates and uploads profiles in batches of 50 |
| `chat(messages, max_tokens=600, **kwargs)` | One chat completion on the shared Qwen model, holding a lock because llama.cpp isn't thread-safe. Extra arguments such as `response_format` go to llama.cpp |
| `answer(question, matches)` | The search page's AI recommendation |

## `agents.py`

### LangChain model

`LocalQwen` is a LangChain `BaseChatModel` that calls `rag.chat`, so agents compose it in ordinary chains (`prompt | LocalQwen() | parser`) and share the one loaded model. Pass `json_schema=` to make llama.cpp constrain output to that JSON schema. Translate uses this with the `ServiceRequest` Pydantic model so a 3B model returns valid JSON.

### Work agents (graph nodes)

Each takes the current `JobState` and returns a partial update. Every update includes one `log` entry made by `log(agent, text)`. The graph appends these to the activity log shown in the console.

| Function | Reads | Writes |
| --- | --- | --- |
| `translate_agent` | `request_text`, `location` | `service_request` |
| `search_match_agent` | `service_request`, `excluded_ids`, `search_round` | `shortlist`, `web_leads`, `excluded_ids`, `search_round` |
| `pricing_agent` | `service_request`, `shortlist`, and the job's media via `config` | `quote_requests` |
| `job_confirmation_agent` | `service_request`, `selected`, `timeslots`, `tenant_name`, `message_feedback` | `confirmation_message` |

### Ranking

| Name | What it does |
| --- | --- |
| `rank_candidates(matches)` | Scores database and web matches on the four criteria and sorts best first. Each result has `id`, `name`, `fields`, `source` (`database` or `web`), `website`, `score`, `breakdown` and `reasons` |
| `license_score`, `review_score`, `cost_score` | Each returns `(score 0–1, reason text)` from a profile's fields |
| `WEIGHTS` | Weights of the four criteria; they should add up to 1 |

### Internet search

| Name | What it does |
| --- | --- |
| `web_search(query, max_results)` | Cached, retried DuckDuckGo tool search. Each result has `title`, `link`, `snippet` and `kind` |
| `link_kind(link)` | `website`, `profile` or `directory`, using `LISTING_SITES` |
| `web_candidates(sr, top_score)` | Internet businesses as candidates in the same shape as database matches, plus directory pages as `leads` |
| `profile_website(fields)` | Best real link already in a profile |
| `find_website(name, location, platform)` | Searches for a technician's site and returns it only if `name_matches` accepts it |
| `name_matches(name, text)` | True when a result's title or URL names the business |

### Helpers

`ServiceRequest` (the Translate output schema and `CATEGORIES`), `rule_urgency` (the hazard-keyword urgency floor), `contact_of` / `contact_of_candidate` (phone, email and website pulled from a candidate), `quote_text` (a quote as display text, e.g. "$180.00 (parts included)"). The prompts are `TRANSLATE_PROMPT`, `QUOTE_PROMPT` and `CONFIRM_PROMPT`.

## `media.py`

| Name | What it does |
| --- | --- |
| `save(job_id, files)` | Async. Validates and stores FastAPI `UploadFile`s, streaming each to disk while enforcing `MAX_BYTES`. All or nothing: on any error it deletes what it wrote and raises `ValueError` |
| `sniff(head)` | `(kind, mime, extension)` from a file's first bytes, or `None` for unsupported formats |
| `list_media(job_id)` | The job's entries from `index.json`: `id`, `kind`, `mime`, `file`, `name`, `size`, `uploaded_at` |
| `file_of(job_id, media_id)` | `(path, entry)` for serving one file; `KeyError` if missing |
| `describe(items)` | Text such as "2 photos and 1 video", used in quote requests |
| `MAX_FILES`, `MAX_BYTES`, `MAX_REQUEST_BYTES` | Limits. `MEDIA_LIMITS` in `static/workflow.js` mirrors them for checks before uploading |

Job IDs are checked to be 32 hex characters before any path is built, and files are saved under generated names, so neither a job ID nor an upload's name can reach other folders. Media lives outside the LangGraph state, so adding files never touches a checkpoint. Agents that need it read it by job ID: `pricing_agent(state, config)` takes the job ID from `config["configurable"]["thread_id"]`.

## `orchestrator.py`

### State

`JobState` is a `TypedDict` checkpointed after every step. Values must stay JSON-like (dicts, lists, strings, numbers) so `SqliteSaver` can store them.

| Key | Set by | Contents |
| --- | --- | --- |
| `request_text`, `location`, `tenant_name` | `start_job` | The tenant's submission |
| `service_request` | Translate | `title`, `category`, `description`, `urgency`, `location`, `search_query` |
| `shortlist` | Search & Match | Top 3 ranked candidates |
| `web_leads` | Search & Match | Internet businesses outside the top 3, then directory pages |
| `excluded_ids`, `search_round` | Search & Match, gates | Rejected technicians; search round counter |
| `quote_requests` | Pricing | Per technician: `tech_id`, `name`, `phone`, `email`, `website`, `message` |
| `quotes` | Pricing gate | `{tech_id: {"amount": float, "notes": str}}` |
| `selected` | Pricing Approval gate | Chosen technician with contact details, `quote` and `quote_text` |
| `timeslots` | Scheduling gate | 2–3 strings |
| `confirmation_message`, `message_feedback`, `confirmed_at` | Job Confirmation | Draft or approved message, owner's change request, approval time |
| `log` | Every node | Activity entries `{agent, text, at}`, appended with `operator.add` |

### Gates

`owner_approval_gate`, `collect_quotes_gate`, `pricing_approval_gate`, `scheduling_gate` and `confirm_message_gate` each call `ask(step)`, which calls `interrupt()` with the step's role and prompt from `PROMPTS`. When resumed, they return a `Command` with a state update and the next node.

### Running jobs

| Function | What it does |
| --- | --- |
| `start_job(request_text, location, tenant_name, job_id=None)` | Records the job and runs the graph in the background; returns the job ID. `app.py` passes a pre-made ID so uploads can be saved and checked first |
| `validate(step, value, state)` | Checks and cleans an answer for a step; raises `ValueError` with a user-facing message |
| `resume(job_id, step, value, role)` | Resumes a waiting job. Raises `ValueError` if that step isn't waiting, `PermissionError` for the wrong role |
| `retry(job_id)` | Continues an `error` or `stalled` job from its last checkpoint |
| `job_view(job_id, role)` | The job as the tenant (limited fields) or owner (everything) sees it. Both include `media` with URLs |
| `list_jobs()` | The 100 most recent jobs with status, for the console |

Only one background run per job is allowed at a time. `_guard` makes the check-and-start atomic, so two clicks can't answer the same step twice.

## Common changes

**Change the selection criteria.** Edit `WEIGHTS`, or a `*_score` function, in `agents.py`. Update the labels in `techCard` in `static/jobs.js` if you add a criterion.

**Add a work agent.** Write a function in `agents.py` that returns a state update plus a `log(...)` entry. Register it in `build_graph` with `g.add_node`, connect it with `add_edge` or route to it from a gate's `Command(goto=...)`, and add it to `NODE_AGENT` (and `AGENTS` if it's a new step in the progress bar). Add any new state keys to `JobState`.

**Add a human step.** Add a gate function that calls `ask("<step>")` and returns a `Command`. Add `"<step>": (role, prompt)` to `PROMPTS`, a branch in `validate`, and a view in `STEP_VIEWS` in `static/jobs.js` (owner) or a form in `static/request.html` (tenant).

**Use a different model.** Agents only depend on a LangChain chat model. Replace `LocalQwen(...)` in the chains with another LangChain chat model (for example a hosted one) and drop `json_schema` in favor of that model's structured output.

**Send messages for real.** Add an email or SMS call in `confirm_message_gate` after approval (and in the Pricing step for quote requests), or in a new work node after it, so it never repeats on resume.

## Testing a change

There is no automated test suite. The quickest end-to-end check drives a job through every step from Python, without the browser:

```python
import time, orchestrator as o

def wait(job_id):
    while (view := o.job_view(job_id, "owner"))["status"] == "working":
        time.sleep(1)
    return view

j = o.start_job("Kitchen faucet keeps dripping", "Santa Clara")
v = wait(j)                                     # waiting at owner_approval
o.resume(j, "owner_approval", {"approved": True}, "owner")
v = wait(j)                                     # waiting at collect_quotes
first = v["quote_requests"][0]["tech_id"]
o.resume(j, "collect_quotes", {"quotes": {first: {"amount": 180}}}, "owner")
wait(j); o.resume(j, "pricing_approval", {"selected_id": first}, "owner")
wait(j); o.resume(j, "scheduling", {"timeslots": ["Mon 9-11am", "Tue 1-3pm"]}, "tenant")
wait(j); o.resume(j, "confirm_message", {"approved": True}, "owner")
print(wait(j)["status"])                        # confirmed
```

It writes to `workflow.db`. To keep test jobs out of the console, set `WORKFLOW_DB=/tmp/test-workflow.db` before running it.
