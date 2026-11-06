# Evals with LangSmith tracing

The 50 test cases in [`../FixIT-Pro_test_cases.xlsx`](../FixIT-Pro_test_cases.xlsx) as executable evals. Each case runs as **one LangSmith trace**, with a child run for every API call, LLM call and tool call it causes, so you can see each case's inputs, outputs, latency, token usage and errors in one place.

```bash
pip install -r evals/requirements.txt
python3 -m evals.run_evals                     # all 50 cases → LangSmith project "fixit-pro-evals"
python3 -m evals.verify_traces evals/results/<run id>
```

Run from the `FixIT-Pro` folder. A full run takes 20–30 minutes on CPU, mostly local Qwen calls.

## Setup

Add your LangSmith key to `.env` (the evals turn tracing on themselves, so you don't need `LANGSMITH_TRACING`):

```bash
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=fixit-pro-evals   # optional
```

No key? `--offline` captures the exact payloads LangSmith would receive to `evals/results/<run>/runs.jsonl`, and `verify_traces` checks those instead.

| Option | Does |
| --- | --- |
| `--only HP-01,KF-03` | Run some cases |
| `--offline` | Capture traces locally instead of sending them |
| `--project NAME` | LangSmith project (default `$LANGSMITH_PROJECT` or `fixit-pro-evals`) |
| `--update-sheet` | Write Status, Observed, Tester, Date run and Trace into the spreadsheet |

## What a trace contains

```
HP-09 Full workflow end to end reaches confirmed        chain   root: case inputs, verdict + checks, eval metadata, feedback
├─ HTTP POST /api/jobs                                   chain   request (form, file names/sizes), status, response
│  ├─ media.save                                         tool
│  └─ repair_workflow                                    chain   LangGraph run (metadata: job_id)
│     ├─ translate → LocalQwen                           llm     prompt, output, input/output tokens, model
│     └─ search_match
│        ├─ pinecone.search                              retriever  query, namespace, documents + scores
│        ├─ web_search → duckduckgo_results_json         tool    query, results, cache_hit
│        ├─ rank_candidates                              tool    scores and reasons per candidate
│        └─ find_website                                 tool
├─ HTTP POST /api/admin/jobs/{id}/resume                 chain
│  └─ repair_workflow → pricing → LocalQwen              llm
└─ …
```

- **Root run**: inputs are the case's ID, title, preconditions, steps and expected result. Outputs are the verdict and every check with its actual value. Metadata covers `eval_run_id`, `case_id`, `label`, `area`, `priority`, `expected`, `code_ref`, the expected child and error runs, model, Pinecone namespace and git SHA/branch/dirty. Tags are `fixit-pro-eval`, the label, priority and area.
- **Feedback** on the root: `passed` (0/1 with the failed checks as the comment), `verdict`, and `checks_passed` (fraction).
- **Verdicts**: `pass` / `fail` for happy paths and edge cases. Known-failure cases check the *correct* behavior, so they're `xfail` while the bug exists and `xpass` once it's fixed. `error` means the case itself crashed (recorded on the root run).
- **Errors**: exceptions in any run are recorded on that run. HTTP calls that return 5xx are recorded as errors on their HTTP run; 4xx responses are expected results and are not errors.

## Trace verification

`verify_traces` reads every case's trace back (from LangSmith, waiting for ingestion, or from `runs.jsonl`) and checks:

| Group | Checks |
| --- | --- |
| root | Root run exists and is the trace root; inputs have the case; outputs have the verdict and checks (or the root has the error) |
| metadata | Required evaluation metadata present and matching the case; eval tags |
| feedback | `passed`, `verdict` and `checks_passed` feedback on the root |
| latency | Every run has start and end times; reports case, LLM and tool latency |
| children | Every child run the case expects (e.g. `llm`, `retriever:pinecone.search`, `chain:pricing*1`) is in the trace, and no child is orphaned |
| complete | Every child run has inputs, and outputs or an error |
| tokens | Every successful LLM run reports input and output tokens |
| errors | Runs the case expects to fail are recorded with an error; 5xx HTTP calls are errors |

It writes `verification.md` (one row per case: verdict, runs, latency, LLM runs, tokens, tool runs, error runs, failed trace checks) and `verification.json`, and exits 1 if any trace check fails.

## Isolation

Evals never touch the app's own data:

- **Workflow DB and uploads**: a temporary `workflow.db` and `uploads/` in `evals/results/<run>/work/` (via `WORKFLOW_DB` / `UPLOADS_DIR`).
- **Local profiles file**: a copy in the same folder.
- **Pinecone**: the sample profiles are upserted to namespace `fixit-pro-evals`. Upload cases write to `fixit-pro-evals-scratch-<run>`, which is deleted afterwards. The app's namespace is never written.

The local Qwen model and the internet search are real. A few cases replace one dependency to force a condition: a Pinecone outage (HP-15), a missing model file (HP-16), no internet results (EC-18, EC-21), and a fixed search result (KF-13).

## Files

| File | What it is |
| --- | --- |
| `catalog.json` | Case metadata, extracted from the spreadsheet |
| `cases.py` | The 50 cases: steps, checks, expected child and error runs |
| `harness.py` | Isolation, the offline capture client, traced API calls, workflow helpers |
| `run_evals.py` | Runs the cases, one trace each, and posts feedback |
| `verify_traces.py` | Checks the traces |
| `update_sheet.py` | Writes results into the spreadsheet |

The app's own tracing (`rag.py`, `agents.py`, `media.py`, `orchestrator.py`) is a no-op unless tracing is on. Set `LANGSMITH_TRACING=true` in `.env` to trace the running app too.
