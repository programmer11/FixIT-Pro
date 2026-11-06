"""Verify the traces of an eval run: one trace per case, with inputs, outputs, latency, tokens, errors and
evaluation metadata on the right runs.

    python3 -m evals.verify_traces evals/results/<run id>

Reads the traces back from LangSmith (or, for an --offline run, from runs.jsonl) and checks every case:

  root        root run exists, is the trace root, has the case inputs and outputs (verdict + checks) or an error
  latency     every run in the trace has start and end times; reports case, LLM and tool latency
  metadata    eval_run_id, case_id, label, area, priority, title, expected, model and git metadata; eval tags
  feedback    passed / verdict / checks_passed feedback on the root run
  children    every child run the case expects (LLM, retriever, tool, workflow node, HTTP call) is in the trace
  complete    every child run has inputs and outputs (or an error) and ended
  tokens      every successful LLM run reports input and output tokens
  errors      runs the case expects to fail are recorded with an error; HTTP calls that returned 5xx are errors

Writes verification.json and verification.md next to results.json. Exit code 1 if any check fails."""
import json, sys, time
from datetime import datetime
from pathlib import Path

REQUIRED_METADATA = ("eval_run_id", "case_id", "label", "area", "priority", "title", "expected", "model", "git_sha")


# ---------- loading runs ----------

def _merge(runs, body, run_id=None):
    rid = str(body.get("id") or run_id)
    runs.setdefault(rid, {}).update({k: v for k, v in body.items() if v is not None})


def load_offline(path):
    runs, feedback = {}, []
    for line in Path(path).read_text().splitlines():
        rec = json.loads(line)
        body, route = rec["body"] or {}, rec["path"]
        if route == "/runs" and rec["method"] == "POST":
            _merge(runs, body)
        elif route.startswith("/runs/") and rec["method"] == "PATCH":
            _merge(runs, body, route.rsplit("/", 1)[-1])
        elif route == "/runs/batch":
            for b in body.get("post", []) + body.get("patch", []):
                _merge(runs, b)
        elif route == "/feedback":
            feedback.append(body)
    return [normalize(r) for r in runs.values()], feedback


def _find_usage(obj):
    """Token usage anywhere in a run payload (LangChain AIMessage usage_metadata or traceable usage_metadata)."""
    if isinstance(obj, dict):
        if "input_tokens" in obj and "output_tokens" in obj:
            return obj
        for v in obj.values():
            found = _find_usage(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_usage(v)
            if found:
                return found
    return None


def _time(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def normalize(r):
    extra = r.get("extra") or {}
    usage = (extra.get("metadata") or {}).get("usage_metadata") or _find_usage(r.get("outputs"))
    return {"id": str(r["id"]), "trace_id": str(r.get("trace_id")), "parent_run_id": r.get("parent_run_id") and str(r["parent_run_id"]),
            "name": r.get("name"), "run_type": r.get("run_type"), "inputs": r.get("inputs"), "outputs": r.get("outputs"),
            "error": r.get("error"), "start_time": _time(r.get("start_time")), "end_time": _time(r.get("end_time")),
            "metadata": extra.get("metadata") or {}, "tags": r.get("tags") or [],
            "input_tokens": (usage or {}).get("input_tokens"), "output_tokens": (usage or {}).get("output_tokens"),
            "total_tokens": (usage or {}).get("total_tokens")}


def load_live(project, root_ids, timeout=180):
    """Read each case's trace from LangSmith, waiting for ingestion to finish."""
    from dotenv import load_dotenv
    from langsmith import Client
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)  # LANGSMITH_API_KEY, as the app does
    client = Client()
    runs, feedback, pending, start = {}, [], set(root_ids), time.time()
    while pending and time.time() - start < timeout:
        for rid in sorted(pending):
            trace = [r for r in client.list_runs(project_name=project, trace_id=rid)]
            root = next((r for r in trace if str(r.id) == rid), None)
            if root and root.end_time and all(r.end_time for r in trace):
                for r in trace:
                    d = r.model_dump()
                    d["outputs"] = d.get("outputs")
                    n = normalize(d)
                    n["input_tokens"] = r.prompt_tokens if r.prompt_tokens is not None else n["input_tokens"]
                    n["output_tokens"] = r.completion_tokens if r.completion_tokens is not None else n["output_tokens"]
                    n["total_tokens"] = r.total_tokens if r.total_tokens is not None else n["total_tokens"]
                    runs[n["id"]] = n
                pending.discard(rid)
        if pending:
            time.sleep(5)
    for f in client.list_feedback(run_ids=list(root_ids)):
        feedback.append({"run_id": str(f.run_id), "key": f.key, "score": f.score, "value": f.value})
    return list(runs.values()), feedback, sorted(pending)


# ---------- checks ----------

def matches(run, selector):
    run_type, _, name = selector.partition(":")
    return run["run_type"] == run_type and (not name or run["name"] == name)


def parse(selector):
    sel, _, n = selector.partition("*")
    return sel, int(n or 1)


def latency(run):
    return (run["end_time"] - run["start_time"]).total_seconds() if run["start_time"] and run["end_time"] else None


def verify_case(case, runs, feedback, run_meta):
    rid = case["root_run_id"]
    trace = [r for r in runs if r["trace_id"] == rid or r["id"] == rid]
    root = next((r for r in trace if r["id"] == rid), None)
    children = [r for r in trace if r["id"] != rid]
    checks = []

    def check(group, name, ok, detail=""):
        checks.append({"group": group, "name": name, "passed": bool(ok), "detail": str(detail)[:300]})

    check("root", "root run exists", root)
    if not root:
        return checks, {}
    check("root", "root is the trace root", root["parent_run_id"] is None and root["trace_id"] == rid)
    check("root", "inputs: case id, steps, expected", (root["inputs"] or {}).get("case_id") == case["id"]
          and (root["inputs"] or {}).get("steps") and (root["inputs"] or {}).get("expected"))
    if case["verdict"] == "error":
        check("root", "error recorded on the root run", root["error"], root["error"])
    else:
        out = root["outputs"] or {}
        check("root", "outputs: verdict and checks", out.get("verdict") == case["verdict"] and isinstance(out.get("checks"), list),
              out.get("verdict"))

    meta = root["metadata"]
    missing = [k for k in REQUIRED_METADATA if not meta.get(k)]
    check("metadata", "evaluation metadata present", not missing, f"missing {missing}" if missing else "")
    check("metadata", "metadata matches the case", meta.get("case_id") == case["id"] and meta.get("label") == case["label"]
          and meta.get("eval_run_id") == run_meta["eval_run_id"], meta.get("case_id"))
    check("metadata", "eval tags", "fixit-pro-eval" in root["tags"] and case["priority"] in root["tags"], root["tags"])
    keys = {f.get("key") for f in feedback if str(f.get("run_id")) == rid}
    check("feedback", "passed / verdict / checks_passed feedback", {"passed", "verdict", "checks_passed"} <= keys, sorted(keys))

    no_end = [r["name"] for r in trace if latency(r) is None or latency(r) < 0]
    check("latency", "every run has start and end times", not no_end, no_end[:5])

    for selector in case["needs"]:
        sel, n = parse(selector)
        found = sum(matches(r, sel) for r in children)
        check("children", f"has {selector}", found >= n, f"found {found}")
    ids = {r["id"] for r in trace}
    orphans = [r["name"] for r in children if r["parent_run_id"] not in ids]
    check("children", "every child's parent is in the trace", not orphans, orphans[:5])
    incomplete = [r["name"] for r in children if r["inputs"] is None or (r["outputs"] is None and not r["error"])]
    check("complete", "children have inputs and outputs or an error", not incomplete, incomplete[:5])

    llm = [r for r in children if r["run_type"] == "llm"]
    no_tokens = [r["name"] for r in llm if not r["error"] and not ((r["input_tokens"] or 0) > 0 and (r["output_tokens"] or 0) > 0)]
    check("tokens", "successful LLM runs report input and output tokens", not no_tokens, no_tokens[:5])

    for selector in case["errors"]:
        sel, n = parse(selector)
        found = sum(matches(r, sel) and bool(r["error"]) for r in children)
        check("errors", f"error recorded on {selector}", found >= n, f"found {found}")
    http_5xx = [r for r in children if r["name"].startswith("HTTP ") and r["error"] and "HTTP 5" in r["error"]]
    http_ok = [r for r in children if r["name"].startswith("HTTP ") and not r["error"]]
    bad = [r["name"] for r in http_ok if ((r["outputs"] or {}).get("status") or 0) >= 500]
    check("errors", "HTTP 5xx responses are recorded as errors", not bad, bad)

    stats = {"runs": len(trace), "case_latency_s": round(latency(root) or 0, 2),
             "llm_runs": len(llm), "llm_latency_s": round(sum(latency(r) or 0 for r in llm), 2),
             "input_tokens": sum(r["input_tokens"] or 0 for r in llm), "output_tokens": sum(r["output_tokens"] or 0 for r in llm),
             "tool_runs": sum(r["run_type"] in ("tool", "retriever") for r in children),
             "tool_latency_s": round(sum(latency(r) or 0 for r in children if r["run_type"] in ("tool", "retriever")), 2),
             "error_runs": sum(bool(r["error"]) for r in children), "http_5xx": len(http_5xx)}
    return checks, stats


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    folder = Path(argv[0])
    results = json.loads((folder / "results.json").read_text())
    roots = [c["root_run_id"] for c in results["cases"]]
    unread = []
    if results["mode"] == "offline":
        runs, feedback = load_offline(folder / "runs.jsonl")
    else:
        runs, feedback, unread = load_live(results["project"], roots)

    report, failed = [], 0
    for case in results["cases"]:
        checks, stats = verify_case(case, runs, feedback, results)
        bad = [c for c in checks if not c["passed"]]
        failed += bool(bad)
        report.append({"id": case["id"], "verdict": case["verdict"], "trace_ok": not bad, "stats": stats, "checks": checks})

    summary = {"eval_run_id": results["eval_run_id"], "mode": results["mode"], "project": results["project"],
               "cases": len(report), "traces_ok": len(report) - failed, "traces_failed": failed, "not_ingested": unread,
               "runs": len(runs), "llm_runs": sum(r["stats"].get("llm_runs", 0) for r in report),
               "input_tokens": sum(r["stats"].get("input_tokens", 0) for r in report),
               "output_tokens": sum(r["stats"].get("output_tokens", 0) for r in report)}
    (folder / "verification.json").write_text(json.dumps({"summary": summary, "cases": report}, indent=1, default=str))

    lines = [f"# Trace verification: {results['eval_run_id']} ({results['mode']})", "",
             f"{summary['traces_ok']}/{summary['cases']} traces pass every check · {summary['runs']} runs · "
             f"{summary['llm_runs']} LLM runs · {summary['input_tokens']:,} input / {summary['output_tokens']:,} output tokens", "",
             "| Case | Verdict | Trace | Runs | Case s | LLM runs | LLM s | Tokens in/out | Tool runs | Error runs | Failed trace checks |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in report:
        s = r["stats"]
        bad = "; ".join(f"{c['name']} ({c['detail']})" for c in r["checks"] if not c["passed"]) or ""
        lines.append(f"| {r['id']} | {r['verdict']} | {'✅' if r['trace_ok'] else '❌'} | {s.get('runs', 0)} | {s.get('case_latency_s', 0)} | "
                     f"{s.get('llm_runs', 0)} | {s.get('llm_latency_s', 0)} | {s.get('input_tokens', 0)}/{s.get('output_tokens', 0)} | "
                     f"{s.get('tool_runs', 0)} | {s.get('error_runs', 0)} | {bad} |")
    (folder / "verification.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    for r in report:
        if not r["trace_ok"]:
            print(f"  {r['id']}: " + "; ".join(f"{c['name']} ({c['detail']})" for c in r["checks"] if not c["passed"]))
    print(f"Report: {folder / 'verification.md'}")
    sys.exit(1 if failed or unread else 0)


if __name__ == "__main__":
    main()
