"""Regenerate evals/catalog.json from FixIT-Pro_test_cases.xlsx after adding or editing a test case.

    python3 -m evals.catalog"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["id", "label", "area", "priority", "title", "preconditions", "steps", "expected", "notes", "status", "code_ref"]


def main():
    import openpyxl
    ws = openpyxl.load_workbook(ROOT / "FixIT-Pro_test_cases.xlsx")["Test cases"]
    rows = [dict(zip(KEYS, [c.value or "" for c in r[:len(KEYS)]])) for r in ws.iter_rows(min_row=2) if r[0].value]
    for row in rows:  # drop result lines that --update-sheet appended, so they don't pile up run after run
        row["notes"] = "\n".join(l for l in str(row["notes"]).splitlines() if not l.startswith("Eval ")).strip()
    (ROOT / "evals" / "catalog.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"Wrote {len(rows)} cases to evals/catalog.json")


if __name__ == "__main__":
    main()
