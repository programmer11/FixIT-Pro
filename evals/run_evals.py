"""Run the FixIT-Pro eval cases, one LangSmith trace per case.

    python3 -m evals.run_evals                  # send traces to LangSmith (needs LANGSMITH_API_KEY)
    python3 -m evals.run_evals --offline        # capture traces to evals/results/<run>/runs.jsonl instead
    python3 -m evals.run_evals --only HP-01,KF-01 --update-sheet

Runs are isolated from the app's real data: a temporary workflow DB and uploads folder, a temporary local
profiles file, and Pinecone namespaces just for evals (fixit-pro-evals, plus scratch/empty namespaces per run
that are deleted afterwards). Results go to evals/results/<run id>/results.json; then run evals.verify_traces."""
import argparse, json, os, subprocess, sys, time, traceback, uuid
from datetime import datetime, timezone
from pathlib import Path

from . import harness

LABEL_ORDER = {"Happy path": 0, "Edge case": 1, "Known failure": 2}


def git_info():
    def git(*args):
        return subprocess.run(["git", *args], cwd=harness.ROOT, capture_output=True, text=True).stdout.strip()
    return {"git_sha": git("rev-parse", "--short", "HEAD"), "git_branch": git("branch", "--show-current"),
            "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def verdict_of(label, checks, error):
    if error:
        return "error"
    passed = bool(checks) and all(ch["passed"] for ch in checks)
    if label == "Known failure":
        return "xpass" if passed else "xfail"
    return "pass" if passed else "fail"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="capture traces locally instead of sending them to LangSmith")
    ap.add_argument("--only", help="comma-separated case IDs")
    ap.add_argument("--project", default=None, help="LangSmith project (default: $LANGSMITH_PROJECT or fixit-pro-evals)")
    ap.add_argument("--update-sheet", action="store_true", help="write results into FixIT-Pro_test_cases.xlsx")
    args = ap.parse_args(argv)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out = harness.ROOT / "evals" / "results" / run_id
    harness.configure(out / "work")

    # App modules read .env and the isolation settings at import.
    import rag  # noqa: E402
    from langsmith import Client, traceable, tracing_context
    from fastapi.testclient import TestClient
    import app  # noqa: E402
    from . import cases  # noqa: F401  (importing registers every case)

    project = args.project or os.getenv("LANGSMITH_PROJECT") or "fixit-pro-evals"
    if args.offline:
        client = harness.capture_client(out / "runs.jsonl")
    elif not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        sys.exit("LANGSMITH_API_KEY isn't set. Add it to FixIT-Pro/.env, or run with --offline to capture traces locally.")
    else:
        client = Client()
    if args.offline:
        project_id = uuid.uuid5(uuid.NAMESPACE_URL, f"offline/{project}")
    else:  # feedback is attached to the project; create it up front so it exists before the first run is ingested
        try:
            project_id = client.read_project(project_name=project).id
        except Exception:
            project_id = client.create_project(project, description="FixIT-Pro eval cases (evals/run_evals.py)").id

    source = harness.ROOT / "santa_clara_home_repair_profiles.json"
    if not source.exists():
        sys.exit(f"{source.name} is needed: the evals upload its profiles to their own Pinecone namespace.")
    profiles = json.loads(source.read_text())
    rag.profiles_path = out / "work" / "santa_clara_home_repair_profiles.json"  # never the app's own local file
    rag.profiles_path.write_text(source.read_text())
    rag.namespace = harness.EVAL_NAMESPACE
    short = run_id.split("-")[-1]
    shared = harness.Shared(http=TestClient(app.app, raise_server_exceptions=False), profiles=profiles,
                            scratch_ns=f"{harness.EVAL_NAMESPACE}-scratch-{short}", empty_ns=f"{harness.EVAL_NAMESPACE}-empty-{short}")

    catalog = {c["id"]: c for c in harness.CATALOG}
    wanted = [i.strip() for i in args.only.split(",")] if args.only else list(catalog)
    missing = [i for i in wanted if i not in harness.CASES or i not in catalog]
    if missing:
        sys.exit(f"Unknown case IDs: {', '.join(missing)}")
    order = sorted(wanted, key=lambda i: (harness.CASES[i].stage, LABEL_ORDER[catalog[i]["label"]], i))
    base_meta = {"eval_run_id": run_id, "eval_suite": "fixit-pro", "eval_mode": "offline" if args.offline else "langsmith",
                 "model": rag.model_name(), "pinecone_namespace": harness.EVAL_NAMESPACE, **git_info()}
    results = {"eval_run_id": run_id, "project": project, "mode": base_meta["eval_mode"], "started": datetime.now(timezone.utc).isoformat(),
               "metadata": base_meta, "cases": []}

    def save():
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(results, indent=1, default=str))

    with tracing_context(enabled=True, client=client, project_name=project):
        @traceable(run_type="chain", name="eval setup")
        def setup(eval_run_id):
            count = rag.upsert_profiles(profiles)
            probe = profiles[0]["Name / company"]
            for _ in range(30):  # new documents take a few seconds to become searchable
                if any(m["id"] == f"technician-{profiles[0]['ID']}" for m in rag.search(probe, top_k=5)):
                    break
                time.sleep(2)
            return {"upserted": count, "namespace": rag.namespace}

        print(f"Eval run {run_id} → {'offline capture' if args.offline else 'LangSmith project ' + project}")
        setup(run_id, langsmith_extra={"metadata": base_meta, "tags": ["fixit-pro-eval", "setup"]})

        for case_id in order:
            meta, spec = catalog[case_id], harness.CASES[case_id]
            root_id = uuid.uuid4()
            metadata = {**base_meta, "case_id": case_id, "label": meta["label"], "area": meta["area"],
                        "priority": meta["priority"], "title": meta["title"], "expected": meta["expected"],
                        "code_ref": meta["code_ref"], "expected_child_runs": list(spec.needs),
                        "expected_error_runs": list(spec.errors), "known_failure": meta["label"] == "Known failure"}
            tags = ["fixit-pro-eval", meta["label"].lower().replace(" ", "-"), meta["priority"], meta["area"]]
            ctx = harness.Ctx(shared)
            record = {"id": case_id, "title": meta["title"], "label": meta["label"], "priority": meta["priority"],
                      "root_run_id": str(root_id), "needs": list(spec.needs), "errors": list(spec.errors)}

            @traceable(run_type="chain", name=f"{case_id} {meta['title']}")
            def run_case(case_id, title, preconditions, steps, expected):
                try:
                    outputs = spec.fn(ctx) or {}
                finally:
                    ctx.finish()
                verdict = verdict_of(meta["label"], ctx.checks, None)
                return {"verdict": verdict, "checks": ctx.checks, "outputs": outputs}

            start, error = time.time(), None
            try:
                run_case(case_id, meta["title"], meta["preconditions"], meta["steps"], meta["expected"],
                         langsmith_extra={"run_id": root_id, "metadata": metadata, "tags": tags})
            except Exception as e:  # recorded on the root run by @traceable; the run continues with the next case
                error = f"{type(e).__name__}: {e}"
                record["traceback"] = traceback.format_exc(limit=6)
            record.update(verdict=verdict_of(meta["label"], ctx.checks, error), checks=ctx.checks, error=error,
                          latency_s=round(time.time() - start, 2))
            failed = [ch for ch in ctx.checks if not ch["passed"]]
            comment = error or ("; ".join(f"{ch['name']} (got {ch['actual']})" for ch in failed) or "all checks passed")
            try:
                client.create_feedback(root_id, key="passed", score=int(record["verdict"] in ("pass", "xpass")),
                                       comment=comment[:1000], trace_id=root_id, session_id=project_id)
                client.create_feedback(root_id, key="verdict", value=record["verdict"], trace_id=root_id, session_id=project_id)
                client.create_feedback(root_id, key="checks_passed", trace_id=root_id, session_id=project_id,
                                       score=round(sum(ch["passed"] for ch in ctx.checks) / max(1, len(ctx.checks)), 3))
            except Exception as e:
                record["feedback_error"] = f"{type(e).__name__}: {e}"
            results["cases"].append(record)
            save()
            print(f"  {case_id:6} {record['verdict']:6} {record['latency_s']:7.1f}s  {meta['title']}"
                  + (f"  [{comment[:120]}]" if record["verdict"] not in ("pass",) else ""))

    for ns in (shared.scratch_ns, shared.empty_ns):  # best effort: the empty namespace usually never existed
        try:
            rag.index.delete_namespace(name=ns)
        except Exception:
            pass
    if hasattr(client, "flush"):
        client.flush()
    if rag._llm is not None:  # free llama.cpp explicitly; freeing it during interpreter shutdown raises noise
        rag._llm.close()
        rag._llm = None
    results["finished"] = datetime.now(timezone.utc).isoformat()
    save()

    counts = {}
    for r in results["cases"]:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("Verdicts:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"Results: {out / 'results.json'}\nVerify:  python3 -m evals.verify_traces {out}")
    if args.update_sheet:
        from .update_sheet import update
        print(update(results))


if __name__ == "__main__":
    main()
