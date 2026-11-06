"""Write an eval run's results into FixIT-Pro_test_cases.xlsx: Status, Observed / notes, Tester, Date run and Trace.

    python3 -m evals.update_sheet evals/results/<run id>"""
import json, sys
from pathlib import Path

from .harness import CATALOG, ROOT

SHEET = ROOT / "FixIT-Pro_test_cases.xlsx"
STATUS = {"pass": "Pass", "fail": "Fail", "error": "Blocked", "xpass": "Fixed"}


def update(results, path=SHEET):
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb["Test cases"]
    headers = [c.value for c in ws[1]]
    if "Trace" not in headers:
        ws.cell(1, len(headers) + 1, "Trace")._style = ws.cell(1, len(headers))._style
        ws.column_dimensions[ws.cell(1, len(headers) + 1).column_letter].width = 44
        headers.append("Trace")
    col = {h: i + 1 for i, h in enumerate(headers)}
    rows = {ws.cell(r, 1).value: r for r in range(2, ws.max_row + 1)}
    notes = {c["id"]: c["notes"] for c in CATALOG}
    planned = {c["id"]: c["status"] for c in CATALOG}
    date = results["started"][:10]
    for case in results["cases"]:
        r = rows.get(case["id"])
        if not r:
            continue
        verdict = case["verdict"]
        status = STATUS.get(verdict) or ("Documented limitation" if planned[case["id"]] == "Documented limitation" else "Reproduced")
        failed = [c for c in case["checks"] if not c["passed"]]
        observed = case["error"] or ("; ".join(f"{c['name']}: got {c['actual']}" for c in failed)
                                     or f"All {len(case['checks'])} checks passed")
        line = f"Eval {date} ({verdict}, {case['latency_s']}s): {observed}"
        ws.cell(r, col["Status"], status)
        ws.cell(r, col["Observed / notes"], f"{notes[case['id']]}\n{line}".strip() if notes.get(case["id"]) else line)
        ws.cell(r, col["Tester"], "evals.run_evals")
        ws.cell(r, col["Date run"], date)
        trace = (f"{results['project']} · run {case['root_run_id']}" if results["mode"] != "offline"
                 else f"offline capture {results['eval_run_id']} · run {case['root_run_id']}")
        ws.cell(r, col["Trace"], trace)
        for name in ("Status", "Observed / notes", "Tester", "Date run", "Trace"):
            ws.cell(r, col[name]).alignment = ws.cell(r, col["Title"]).alignment.copy()
    wb.save(path)
    return f"Updated {len(results['cases'])} rows in {path.name}"


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    print(update(json.loads((folder / "results.json").read_text())))
