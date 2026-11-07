# Evaluations and tracing

FixIT-Pro is evaluated in two ways, both recorded in [LangSmith](https://smith.langchain.com):

| Suite | Question it answers | Source | Runs as |
| --- | --- | --- | --- |
| **Test cases** | Does the app behave correctly: happy paths, edge cases, known bugs? | [`FixIT-Pro_test_cases.xlsx`](../FixIT-Pro_test_cases.xlsx), 50 cases | One LangSmith trace per case, in a project you choose |
| **Golden dataset** | How good are its outputs, and where and why do they fail? | [`FixIT-Pro_golden_dataset.xlsx`](../FixIT-Pro_golden_dataset.xlsx), 118 examples | A LangSmith experiment on the dataset `fixit-pro-golden` |

The latest results, and what changed to get them, are in the [evaluation report](eval-report.md). Command options are listed in [`evals/README.md`](../evals/README.md).

## Quick start

```bash
pip install -r evals/requirements.txt
# .env needs LANGSMITH_API_KEY (plus the app's own Pinecone and Qwen settings)

python3 -m evals.run_evals --project fixit-local --update-sheet   # test cases (~15 min)
python3 -m evals.verify_traces evals/results/<run id>             # check every trace
python3 -m evals.golden --compare evals/results/golden-<before>   # golden experiment + before/after (~20 min)
```

Most of the time is the local Qwen model on CPU. `--offline` runs the test cases without a LangSmith key and captures the traces to a local file instead.

## Tracing

When tracing is on, the app records a LangSmith run for every model call, Pinecone search and upload, internet search, website lookup, ranking and file upload. The evals switch tracing on for themselves; set `LANGSMITH_TRACING=true` in `.env` to trace the running app too.

A test case's trace looks like this:

```
HP-09 Full workflow end to end reaches confirmed        chain      case inputs, verdict and checks, eval metadata, feedback
├─ HTTP POST /api/jobs                                   chain      request, status, response
│  ├─ media.save                                         tool
│  └─ repair_workflow                                    chain      LangGraph run, metadata: job_id
│     ├─ translate → LocalQwen                           llm        prompt, output, input/output tokens, model
│     └─ search_match
│        ├─ pinecone.search                              retriever  query, namespace, documents and scores
│        ├─ web_search → duckduckgo_results_json         tool       query, results, cache_hit
│        ├─ rank_candidates                              tool       scores and reasons per candidate
│        └─ find_website                                 tool
└─ HTTP POST /api/admin/jobs/{id}/resume → … → pricing → LocalQwen
```

- **Inputs and outputs** are on every run. HTTP runs show the request (file names and sizes, never file contents; the admin password masked) and the response.
- **Latency** comes from each run's start and end time.
- **Tokens** come from llama.cpp's usage, on every LLM run.
- **Errors** are recorded on the run that raised them. An HTTP call that returns 5xx is recorded as an error; 4xx responses are expected results.
- **Evaluation metadata** on each case's root run: `eval_run_id`, `case_id`, `label`, `area`, `priority`, `expected`, `code_ref`, the expected child and error runs, model, Pinecone namespace and git commit. Tags: `fixit-pro-eval`, the label, priority and area.
- **Feedback** on each root run: `passed` (with the failed checks as the comment), `verdict` and `checks_passed`.

`verify_traces` reads every trace back and checks all of the above, including that each case contains the child runs it should (for example an LLM run and a retriever run) and that runs expected to fail are recorded as errors. It writes `verification.md` and exits with an error if any trace is incomplete.

## Test cases

Each case has a label:

| Label | Meaning | Verdicts |
| --- | --- | --- |
| Happy path | Normal use that must work | `pass` / `fail` |
| Edge case | Limits, invalid input, permissions, recovery | `pass` / `fail` |
| Known failure | A bug or limitation already found | `xfail` while it still happens, `xpass` once fixed |

Known-failure cases check the correct behavior, so a fix shows up as `xpass` without editing the case. `--update-sheet` writes each run's verdicts, observations and trace references back into the spreadsheet.

## Golden dataset

| Sheet | Input → correct output | Metrics |
| --- | --- | --- |
| Retrieval | Search query → technicians graded 2 (lists the service) or 1 (related) | hit@3, precision@3, nDCG@3 |
| Translate | Tenant request → category and urgency, facts the description must keep | category/urgency exact and acceptable, fact coverage |
| Ranking | Profile fields → license, review and cost scores from the spec in [workflow.md](workflow.md) | absolute error |
| Recommendation | Question → acceptable technicians, required facts and caveats, forbidden claims | provider precision, rubric checks |
| Messages | Job details → what quote and confirmation messages must and mustn't contain | rubric checks |

The golden labels were made against profiles `001`–`036`. The run loads exactly those into its own Pinecone namespace, `fixit-pro-golden-v1`, so adding technicians to the app doesn't change golden scores.

### Failure modes and cost

Each failed check is labeled with a failure mode, such as `TR-CAT` (wrong job category) or `REC-CLAIM` (a license claim the profile doesn't support). The report groups modes into root-cause families, finds the dominant mode by count and by estimated cost, and estimates each mode's monthly cost:

> incidents per month = failure rate × monthly volume · cost per incident = owner minutes × hourly rate + probability × direct cost

The dollar inputs are assumptions (100 requests a month, $60 an hour of owner time, and so on) on the Assumptions sheet of `FixIT-Pro_golden_eval.xlsx`; change them there and the costs recalculate.

### Comparing runs

`--compare` reports, for the examples both runs share: pass rate and metric means per sheet, the examples that were fixed or regressed, and failure-mode counts. Use it for every change to a prompt or a check.

## Isolation

The evals never touch the app's data:

- a temporary workflow database and uploads folder per run (`evals/results/<run>/work/`);
- a copy of the profiles file;
- their own Pinecone namespaces: `fixit-pro-evals` for test cases (with a per-run scratch namespace for upload cases, deleted afterwards) and `fixit-pro-golden-v1` for the golden dataset.

The local model and the internet search are real. A few cases replace one dependency to force a condition: a Pinecone outage, a missing model file, no internet results, and a fixed search result.

## Adding to the evals

- **A test case:** add a row to the spreadsheet, run `python3 -m evals.catalog` to regenerate `evals/catalog.json`, and add a function with the same ID to `evals/cases.py` using `@case("XX-NN", needs=..., errors=...)`. `needs` lists the child runs its trace must contain.
- **A golden example:** add a row to the right sheet of the golden dataset. The next run syncs it to LangSmith. Recommendation and Messages rows also need their rubric in `REC_RULES` or `MSG_INPUTS` in `evals/golden.py`.
- **After changing a check or a label:** `python3 -m evals.golden --rescore evals/results/golden-<run>` re-scores saved outputs and replaces the experiment's feedback, without running the model again. Record label changes in the golden dataset's About sheet.

## Limits

- Golden failure rates reflect the golden set, not production traffic, and cost figures rest on assumptions.
- Message and recommendation checks are pattern-based: they can miss a paraphrased omission or flag a harmless mention (for example a message that quotes an injected instruction without following it).
- The local model runs at temperature 0, so reruns are close to deterministic, but results still shift when the profile text or prompts change. Compare runs rather than single scores.
