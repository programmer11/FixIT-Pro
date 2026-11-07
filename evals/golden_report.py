"""Analysis workbook for a golden-dataset experiment: aggregate scores linked to example traces and test cases,
failure modes clustered by cause, the dominant mode, and a cost model you can edit.

    python3 -m evals.golden_report evals/results/golden-<run>

Writes FixIT-Pro_golden_eval.xlsx (and analysis.json in the run folder)."""
import json, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path

from .harness import ROOT

REPORT = ROOT / "FixIT-Pro_golden_eval.xlsx"
WEB = "https://smith.langchain.com"

# code: (component, title, root-cause family, probable cause, owner minutes, direct-cost term, volume, rate basis)
# direct-cost term: (multiplier, assumption a, assumption b) → multiplier × a × b
MODES = {
 "RET-MISS": ("Retrieval", "No relevant technician in the top 3", "Retrieval can't tell services apart",
              "Each profile is embedded as ~1,080 characters, mostly boilerplate (ratings, sources, notes); the ~100-character "
              "service list barely moves the vector. A few 'hub' profiles sit near every query: {hubs}. Top-10 similarity "
              "spread averages {spread} (an off-topic query, R30, scores as high as on-topic ones).", 10, None, "requests", "golden"),
 "RET-INTRUDER": ("Retrieval", "Off-topic specialist in the top 3", "Retrieval can't tell services apart",
                  "Same hub effect: {intruders}.", 5, None, "requests", "golden"),
 "REC-UPSTREAM": ("Recommendation", "Recommendation fails because retrieval offered no acceptable provider",
                  "Retrieval can't tell services apart", "The model may only use the 3 retrieved profiles.", 15, None, "searches", "golden"),
 "REC-OFFLIST": ("Recommendation", "Recommends an unacceptable provider (acceptable ones were retrieved)", "LLM judgment",
                 "Model picks a retrieved profile that doesn't fit.", 10, None, "searches", "golden"),
 "REC-UNGROUNDED": ("Recommendation", "Contact detail or price not in the profiles", "LLM omits or invents content",
                    "Hallucinated or reformatted contact/price.", 10, None, "searches", "golden"),
 "REC-CLAIM": ("Recommendation", "Forbidden claim (licensed, available, verified)", "LLM omits or invents content",
               "Model overstates what profiles say.", 5, (1, "p_liability", "liability"), "searches", "golden"),
 "REC-CAVEAT": ("Recommendation", "Required caveat missing (license conflict, availability, area)", "LLM omits or invents content",
                "Model drops a caveat stated in the profile.", 5, (0.5, "p_liability", "liability"), "searches", "golden"),
 "REC-OMIT": ("Recommendation", "Required fact missing (license number, fee, phone)", "LLM omits or invents content",
              "Model leaves out a labeled field.", 3, None, "searches", "golden"),
 "REC-ABSTAIN": ("Recommendation", "Recommends someone when nobody fits", "LLM judgment",
                 "Model doesn't abstain on out-of-scope requests.", 5, None, "searches", "golden"),
 "REC-INJECTION": ("Recommendation", "Follows an instruction embedded in the question", "LLM omits or invents content",
                   "Prompt injection.", 10, None, "searches", "golden"),
 "TR-CAT": ("Translate", "Wrong job category (Qwen)", "LLM judgment",
            "Qwen 2.5 3B picks the wrong trade under the JSON category enum; wrong picks: {tr_cat}. HVAC sits near the end "
            "of the enum, suggesting position bias rather than misreading.", 10,
            (1, "p_wrong_visit", "wrong_trade_visit"), "requests", "golden"),
 "TR-CAT-RULES": ("Translate", "Wrong job category (keyword fallback)", "Keyword rules misfire",
                  "Substring keyword match (e.g. 'hang' in 'change').", 10, (1, "p_wrong_visit", "wrong_trade_visit"), "requests", "golden"),
 "TR-URG-FLOOR": ("Translate", "Over-escalated urgency forced by the keyword floor", "Keyword rules misfire",
                  "The hazard/urgency regex (no word boundaries, no negation) sets a floor the model can't lower.", 2,
                  (1, "p_emergency_dispatch", "emergency_premium"), "requests", "golden"),
 "TR-URG-OVER": ("Translate", "Over-escalated urgency (model)", "LLM judgment", "Model rates the job more urgent than the prompt rules.", 2,
                 (1, "p_emergency_dispatch", "emergency_premium"), "requests", "golden"),
 "TR-URG-UNDER": ("Translate", "Under-escalated urgency (missed emergency/high)", "LLM judgment",
                  "Model rates a hazard as routine and no keyword catches it.", 2, (1, "p_damage", "missed_emergency_damage"), "requests", "golden"),
 "TR-FACT": ("Translate", "Description drops a fact the tenant gave", "LLM omits or invents content",
             "Model paraphrases away a detail.", 5, None, "requests", "golden"),
 "TR-LEAK": ("Translate", "Injected or invented content in the description", "LLM omits or invents content",
             "Prompt injection or invention.", 10, None, "requests", "golden"),
 "RANK-FEE-PARSE": ("Ranking", "Fee parsed wrongly (thousands separator)", "Scoring parser/spec gaps",
                    "cost_score's regex stops at the comma: $1,200 → $1.", 15, (1, "p_overpay", "overpay"), "requests", "profiles"),
 "RANK-LICENSE-RULE": ("Ranking", "Explicit 'unlicensed' scored as a conflict", "Scoring parser/spec gaps",
                       "license_score checks 'unlicensed' with the conflict rule (0.2) instead of 0.", 5,
                       (1, "p_liability", "liability"), "requests", "profiles"),
 "RANK-REVIEW": ("Ranking", "Review score differs from spec", "Scoring parser/spec gaps", "", 5, None, "requests", "profiles"),
 "MSG-STRUCT": ("Messages", "Greeting/sign-off/body structure wrong", "LLM omits or invents content", "", 2, None, "messages", "golden"),
 "MSG-OMIT": ("Messages", "Message leaves out a required element", "LLM omits or invents content", "", 3, None, "messages", "golden"),
 "MSG-INVENT": ("Messages", "Message invents a price or date", "LLM omits or invents content", "", 10, (0.1, "p_overpay", "overpay"), "messages", "golden"),
 "MSG-PLACEHOLDER": ("Messages", "Placeholder or subject line in the message", "LLM omits or invents content", "", 2, None, "messages", "golden"),
 "MSG-LENGTH": ("Messages", "Message over the word limit", "LLM omits or invents content", "", 2, None, "messages", "golden"),
 "MSG-INJECTION": ("Messages", "Message follows an embedded instruction", "LLM omits or invents content", "", 5, None, "messages", "golden"),
 "ERROR": ("Any", "Example crashed", "Harness/app errors", "", 5, None, "requests", "golden"),
}
ASSUMPTIONS = [  # key, value, unit, meaning
 ("requests_per_month", 100, "requests", "Tenant repair requests per month (each runs Translate, Search & Match, ranking)"),
 ("searches_per_month", 60, "searches", "Owner searches with an AI recommendation per month"),
 ("messages_per_job", 2, "messages", "Drafted messages per job (quote request body + confirmation)"),
 ("owner_rate", 60, "$/hour", "Value of the property owner's time"),
 ("emergency_premium", 150, "$", "Extra charged for an emergency call-out vs a normal visit"),
 ("p_emergency_dispatch", 0.5, "probability", "Chance an over-escalated job is actually dispatched at emergency rates"),
 ("missed_emergency_damage", 2000, "$", "Damage when a real emergency waits (e.g. water damage)"),
 ("p_damage", 0.1, "probability", "Chance an under-escalated emergency causes that damage"),
 ("wrong_trade_visit", 120, "$", "Cost of a visit by the wrong trade"),
 ("p_wrong_visit", 0.15, "probability", "Chance a wrong-category job reaches a visit before it's caught"),
 ("liability", 5000, "$", "Exposure from hiring an unlicensed contractor based on a wrong claim"),
 ("p_liability", 0.01, "probability", "Chance a wrong license claim leads to that exposure"),
 ("overpay", 100, "$", "Overpayment when the cheapest-looking quote isn't"),
 ("p_overpay", 0.2, "probability", "Chance a mis-ranked fee leads to overpaying"),
]


def latest_test_results():
    best = None
    for f in (ROOT / "evals" / "results").glob("*/results.json"):
        r = json.loads(f.read_text())
        if r.get("mode") == "langsmith" and len(r.get("cases", [])) >= 50 and (not best or r["started"] > best["started"]):
            best = r
    return best


def profile_prevalence():
    """Share of the 36 real profiles each ranking mode affects (ranking failures depend on data, not on queries)."""
    ps = json.loads((ROOT / "santa_clara_home_repair_profiles.json").read_text())
    fee = sum(bool(re.search(r"\$\s?\d{1,3},\d{3}", p.get("Service fee / minimum") or "")) for p in ps)
    unl = sum("unlicensed" in (p.get("License evidence") or "").lower() and "conflict" not in (p.get("License evidence") or "").lower() for p in ps)
    return {"RANK-FEE-PARSE": (fee, len(ps)), "RANK-LICENSE-RULE": (unl, len(ps)), "RANK-REVIEW": (0, len(ps))}


def fetch_trace_stats(client, examples, timeout=240):
    """Tokens and latency per example from LangSmith (root run of each example's trace), waiting for ingestion."""
    stats, pending, start = {}, {e["id"]: e["run_id"] for e in examples}, time.time()
    while pending and time.time() - start < timeout:
        for gid, rid in list(pending.items()):
            try:
                r = client.read_run(rid)
            except Exception:
                continue
            if r.end_time and (r.total_tokens is not None or r.run_type != "llm"):
                stats[gid] = {"latency_s": round((r.end_time - r.start_time).total_seconds(), 2), "total_tokens": r.total_tokens or 0,
                              "prompt_tokens": r.prompt_tokens or 0, "completion_tokens": r.completion_tokens or 0, "error": r.error}
                pending.pop(gid)
        if pending:
            time.sleep(5)
    return stats


def build(folder):
    import warnings
    warnings.filterwarnings("ignore")
    from dotenv import load_dotenv
    from langsmith import Client
    load_dotenv(ROOT / ".env", override=True)
    folder = Path(folder)
    g = json.loads((folder / "golden_results.json").read_text())
    ex = g["examples"]
    client = Client()
    base = f"{WEB}/o/{g['tenant_id']}"
    exp_url = f"{base}/datasets/{g['dataset_id']}/compare?selectedSessions={g['experiment_id']}"
    trace_url = lambda e: f"{base}/projects/p/{g['experiment_id']}/r/{e['run_id']}"

    # ---- link to test cases (the latest full LangSmith run of evals.run_evals)
    tests = latest_test_results()
    test_by_id, test_url = {}, lambda c: ""
    if tests:
        pid = client.read_project(project_name=tests["project"]).id
        test_by_id = {c["id"]: c for c in tests["cases"]}
        test_url = lambda c: f"{base}/projects/p/{pid}/r/{c['root_run_id']}"

    # ---- read back from LangSmith: per-example tokens/latency, and the experiment's feedback (to confirm aggregates)
    stats = fetch_trace_stats(client, ex)
    fb = defaultdict(dict)
    for f in client.list_feedback(run_ids=[e["run_id"] for e in ex], feedback_key=["pass"]):  # unfiltered listing truncates
        fb[str(f.run_id)][f.key] = f.score if f.score is not None else f.value
    for e in ex:
        e["ls"] = stats.get(e["id"], {})
        e["ls_pass"] = fb.get(e["run_id"], {}).get("pass")

    # ---- aggregates
    applicable = lambda e: not (e["sheet"] == "Retrieval" and e["metrics"].get("applicable") == 0)
    sheets = ["Retrieval", "Translate", "Ranking", "Recommendation", "Messages"]
    agg = []
    for sh in sheets + ["All"]:
        group = [e for e in ex if (sh == "All" or e["sheet"] == sh) and applicable(e)]
        if not group:
            continue
        metric_names = sorted({k for e in group for k, v in e["metrics"].items() if isinstance(v, (int, float)) and k != "applicable"})
        means = {}
        for m in metric_names:
            vals = [e["metrics"][m] for e in group if isinstance(e["metrics"].get(m), (int, float))]
            means[m] = round(sum(vals) / len(vals), 3) if vals else None
        failing = [e for e in group if not e["passed"]]
        ls_pass = [e["ls_pass"] for e in group if e["ls_pass"] is not None]
        agg.append({"sheet": sh, "n": len(group), "passed": len(group) - len(failing), "pass_rate": round(1 - len(failing) / len(group), 3),
                    "ls_pass_rate": round(sum(ls_pass) / len(ls_pass), 3) if ls_pass else None, "ls_feedback_n": len(ls_pass),
                    "means": means if sh != "All" else {}, "failing": [e["id"] for e in failing],
                    "failing_tests": sorted({e["related_test"] for e in failing if e["related_test"]}),
                    "by_tag": {t: f"{sum(e['passed'] for e in group if e['tag'] == t)}/{sum(e['tag'] == t for e in group)}"
                               for t in sorted({e["tag"] for e in group})},
                    "latency_s": round(sum(e["ls"].get("latency_s") or e.get("latency_s") or 0 for e in group), 1),
                    "tokens": sum(e["ls"].get("total_tokens", 0) for e in group)})

    # ---- retrieval diagnostics: hub profiles (in the top 3 for many unrelated queries) and score spread
    ret = [e for e in ex if e["sheet"] == "Retrieval" and e.get("outputs")]
    top_counts = Counter(t for e in ret for t in e["outputs"]["top3"])
    names = {p["ID"]: p["Name / company"] for p in json.loads((ROOT / "santa_clara_home_repair_profiles.json").read_text())}
    hubs = ", ".join(f"{i} {names[i][:24]} in {n}/{len(ret)} top-3s" for i, n in top_counts.most_common(3)) if ret else "n/a"
    spreads = [e["outputs"]["spread"] for e in ret if e["outputs"].get("spread")]
    intr = Counter(t for e in ret for c in e["checks"] if c["mode"] == "RET-INTRUDER" for t in re.findall(r"\d{3}", c["detail"]))
    wrong = Counter(e["outputs"]["service_request"]["category"] for e in ex if "TR-CAT" in e["modes"])
    diag = {"tr_cat": ", ".join(f"{k} ×{v}" for k, v in wrong.most_common()) or "n/a", "hubs": hubs, "spread": round(sum(spreads) / len(spreads), 3) if spreads else "n/a",
            "intruders": ", ".join(f"{i} {names[i][:28]} intrudes on {n} queries" for i, n in intr.most_common(2)) or "n/a"}

    # ---- cluster failures into modes
    comp_n = Counter(e["sheet"] for e in ex if applicable(e))
    prevalence = profile_prevalence()
    clusters = []
    for code, (comp, title, family, cause, minutes, direct, volume, basis) in MODES.items():
        members = [e for e in ex if code in e["modes"]]
        if not members:
            continue
        n_app = comp_n.get(comp, len(ex))
        if basis == "profiles":
            affected, total = prevalence[code]
            rate, rate_note = affected / total, f"{affected}/{total} real profiles"
        else:
            rate, rate_note = len(members) / n_app, f"{len(members)}/{n_app} golden examples"
        details = [c["detail"] for e in members for c in e["checks"] if c["mode"] == code][:3]
        clusters.append({"code": code, "component": comp, "title": title, "family": family, "cause": cause.format(**diag),
                         "examples": [e["id"] for e in members], "count": len(members), "applicable": n_app,
                         "golden_rate": round(len(members) / n_app, 3), "rate": round(rate, 4), "rate_note": rate_note,
                         "related_tests": sorted({e["related_test"] for e in members if e["related_test"]}),
                         "evidence": details, "minutes": minutes, "direct": direct, "volume": volume,
                         "avg_latency_s": round(sum(e["ls"].get("latency_s") or 0 for e in members) / len(members), 1),
                         "avg_tokens": round(sum(e["ls"].get("total_tokens", 0) for e in members) / len(members)),
                         "trace_urls": [trace_url(e) for e in members[:3]]})

    A = {k: v for k, v, _, _ in ASSUMPTIONS}
    volume_of = {"requests": A["requests_per_month"], "searches": A["searches_per_month"],
                 "messages": A["requests_per_month"] * A["messages_per_job"]}
    for c in clusters:
        per = c["minutes"] / 60 * A["owner_rate"] + (c["direct"][0] * A[c["direct"][1]] * A[c["direct"][2]] if c["direct"] else 0)
        c["cost_per_incident"] = round(per, 2)
        c["incidents_per_month"] = round(c["rate"] * volume_of[c["volume"]], 2)
        c["monthly_cost"] = round(c["incidents_per_month"] * per, 2)
    total_cost = sum(c["monthly_cost"] for c in clusters) or 1
    for c in clusters:
        c["cost_share"] = round(c["monthly_cost"] / total_cost, 3)
    clusters.sort(key=lambda c: (-c["count"], -c["monthly_cost"]))

    families = defaultdict(lambda: {"modes": [], "examples": set(), "monthly_cost": 0})
    for c in clusters:
        f = families[c["family"]]
        f["modes"].append(c["code"])
        f["examples"] |= set(c["examples"])
        f["monthly_cost"] += c["monthly_cost"]
    fam = sorted(({"family": k, "modes": v["modes"], "examples": sorted(v["examples"]), "count": len(v["examples"]),
                   "monthly_cost": round(v["monthly_cost"], 2)} for k, v in families.items()), key=lambda f: -f["count"])
    dominant_count = clusters[0] if clusters else None
    dominant_cost = max(clusters, key=lambda c: c["monthly_cost"]) if clusters else None

    analysis = {"experiment": g["experiment"], "experiment_url": exp_url, "aggregates": agg, "clusters": clusters, "families": fam,
                "dominant_by_count": dominant_count and dominant_count["code"], "dominant_by_cost": dominant_cost and dominant_cost["code"],
                "dominant_family": fam[0]["family"] if fam else None, "total_monthly_cost": round(total_cost, 2),
                "test_run": tests and tests["eval_run_id"]}
    (folder / "analysis.json").write_text(json.dumps(analysis, indent=1, default=str))
    # Every run gets its own workbook; only a run of every sheet replaces the project's main report.
    full = g["metadata"].get("sheets", "all") == "all"
    paths = [folder / "golden_eval.xlsx"] + ([REPORT] if full else [])
    write_workbook(g, ex, agg, clusters, fam, analysis, trace_url, test_by_id, test_url, paths)
    a = analysis
    return (f"Report: {', '.join(p.name for p in paths)} · pass {agg[-1]['passed']}/{agg[-1]['n']} · dominant by count {a['dominant_by_count']}, "
            f"by cost {a['dominant_by_cost']} · est. ${a['total_monthly_cost']:,.0f}/month")


# ======================================================================= workbook

def write_workbook(g, ex, agg, clusters, fam, analysis, trace_url, test_by_id, test_url, paths):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    HDR, thin = PatternFill("solid", fgColor="1F3A5F"), Side(style="thin", color="D0D7DE")
    INPUT = PatternFill("solid", fgColor="FFF4CE")
    link_font = Font(color="1F5FBF", underline="single")

    def table(ws, headers, rows, widths, start=1):
        for j, h in enumerate(headers, 1):
            c = ws.cell(start, j, h)
            c.font, c.fill = Font(bold=True, color="FFFFFF"), HDR
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for i, row in enumerate(rows, start + 1):
            for j, v in enumerate(row, 1):
                c = ws.cell(i, j)
                if isinstance(v, tuple) and v and v[0] == "link":
                    c.value, c.hyperlink, c.font = v[2], v[1], link_font
                else:
                    c.value = v
                c.alignment = Alignment(wrap_text=True, vertical="top")
                c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        return start + len(rows)

    # ---------- Summary
    ws = wb.active
    ws.title = "Summary"
    tot = agg[-1]
    dc = next(c for c in clusters if c["code"] == analysis["dominant_by_count"]) if clusters else None
    dk = next(c for c in clusters if c["code"] == analysis["dominant_by_cost"]) if clusters else None
    lines = [
        ("FixIT-Pro golden dataset evaluation", Font(bold=True, size=15)),
        (f"Experiment {g['experiment']} · {g['finished'][:10]} · model {g['metadata'].get('model')} · git {g['metadata'].get('git_sha')}", None),
        (("link", analysis["experiment_url"], "Open the experiment in LangSmith (aggregate scores, every example's trace)"), None),
        ("", None),
        (f"Overall: {tot['passed']}/{tot['n']} examples pass ({tot['pass_rate']:.0%}). LangSmith 'pass' feedback agrees: "
         f"{tot['ls_pass_rate']:.0%} over {tot['ls_feedback_n']} runs." if tot["ls_pass_rate"] is not None else
         f"Overall: {tot['passed']}/{tot['n']} examples pass ({tot['pass_rate']:.0%}).", Font(bold=True)),
    ]
    if dc:
        lines += [
            (f"Dominant failure mode (most examples): {dc['code']} — {dc['title']}: {dc['count']} examples "
             f"({', '.join(dc['examples'])}). Family: {dc['family']}.", Font(bold=True)),
            (f"Costliest failure mode (est.): {dk['code']} — {dk['title']}: ${dk['monthly_cost']:,.0f}/month of "
             f"${analysis['total_monthly_cost']:,.0f}/month estimated across all modes ({dk['cost_share']:.0%}).", Font(bold=True)),
            (f"Dominant root-cause family: {fam[0]['family']} — {fam[0]['count']} failing examples across {', '.join(fam[0]['modes'])}, "
             f"${fam[0]['monthly_cost']:,.0f}/month.", None),
        ]
    lines += [("", None), ("Costs are estimates from the editable assumptions on the 'Cost model' sheet; failure rates come from "
                           "the golden set (not production traffic), except ranking modes, which use prevalence in the 36 real profiles.", None)]
    for text, font in lines:
        ws.append([None])
        c = ws.cell(ws.max_row, 1)
        if isinstance(text, tuple):
            c.value, c.hyperlink, c.font = text[2], text[1], link_font
        else:
            c.value = text
            if font:
                c.font = font
        c.alignment = Alignment(wrap_text=True)
    ws.column_dimensions["A"].width = 150
    r = ws.max_row + 2
    ws.cell(r, 1, "Root-cause families").font = Font(bold=True, size=12)
    fam_ws = wb.create_sheet("Families")
    table(fam_ws, ["Family", "Failing examples", "Count", "Modes", "Est. $/month"],
          [[f["family"], ", ".join(f["examples"]), f["count"], ", ".join(f["modes"]), f["monthly_cost"]] for f in fam], [34, 60, 8, 40, 12])
    ws.cell(r + 1, 1, "See the Families, Failure modes and Cost model sheets.")

    # ---------- Aggregates (linked to examples and test cases)
    am = wb.create_sheet("Aggregates")
    rows = []
    for a in agg:
        means = "; ".join(f"{k} {v}" for k, v in a["means"].items() if v is not None)
        tests = "; ".join(f"{t} ({test_by_id[t]['verdict']})" if t in test_by_id else t for t in a["failing_tests"])
        rows.append([a["sheet"], a["n"], a["passed"], a["pass_rate"], a["ls_pass_rate"], means,
                     "; ".join(f"{k} {v}" for k, v in a["by_tag"].items()), ", ".join(a["failing"]), tests, a["latency_s"], a["tokens"]])
    table(am, ["Sheet", "Examples", "Passed", "Pass rate", "Pass rate (LangSmith feedback)", "Mean metrics", "Passed by tag",
               "Failing examples", "Related test cases (verdict)", "Total latency s", "Tokens"], rows,
          [14, 9, 8, 9, 12, 60, 34, 40, 34, 10, 9])
    for i in range(2, am.max_row + 1):
        am.cell(i, 4).number_format = am.cell(i, 5).number_format = "0%"

    # ---------- Failure modes
    fm = wb.create_sheet("Failure modes")
    rows = [[c["code"], c["component"], c["title"], c["family"], c["count"], c["applicable"], c["golden_rate"], ", ".join(c["examples"]),
             ", ".join(c["related_tests"]), c["cause"], "\n".join(c["evidence"]), c["avg_latency_s"], c["avg_tokens"],
             ("link", c["trace_urls"][0], "example trace") if c["trace_urls"] else ""] for c in clusters]
    table(fm, ["Mode", "Component", "What fails", "Root-cause family", "Examples", "Applicable", "Golden failure rate", "Example IDs",
               "Related tests", "Probable cause", "Evidence (from checks)", "Avg latency s", "Avg tokens", "Trace"], rows,
          [16, 14, 40, 26, 9, 10, 10, 30, 14, 50, 60, 9, 9, 14])
    for i in range(2, fm.max_row + 1):
        fm.cell(i, 7).number_format = "0%"
    if clusters:
        fm.cell(2, 1).font = Font(bold=True, color="B42318")

    # ---------- Assumptions + Cost model (live formulas)
    asm = wb.create_sheet("Assumptions")
    table(asm, ["Key", "Value", "Unit", "Meaning"], [[k, v, u, m] for k, v, u, m in ASSUMPTIONS], [24, 10, 12, 80])
    cell = {k: f"Assumptions!$B${i}" for i, (k, *_) in enumerate(ASSUMPTIONS, 2)}
    for i in range(2, len(ASSUMPTIONS) + 2):
        asm.cell(i, 2).fill = INPUT
    asm.cell(len(ASSUMPTIONS) + 3, 1, "Yellow cells are inputs: change them and the Cost model sheet recalculates.")

    cm = wb.create_sheet("Cost model")
    vol_formula = {"requests": cell["requests_per_month"], "searches": cell["searches_per_month"],
                   "messages": f"{cell['requests_per_month']}*{cell['messages_per_job']}"}
    rows = []
    for c in clusters:
        rows.append([c["code"], c["title"], c["rate"], c["rate_note"], None, c["minutes"], None, None, None, None, None])
    table(cm, ["Mode", "What fails", "Failure rate", "Rate basis", "Monthly volume", "Owner minutes / incident", "Direct $ / incident",
               "Cost / incident", "Incidents / month", "Est. $ / month", "Share"], rows, [16, 44, 10, 22, 11, 11, 13, 11, 11, 12, 8])
    last = len(clusters) + 1
    for i, c in enumerate(clusters, 2):
        cm.cell(i, 5, f"={vol_formula[c['volume']]}")
        d = c["direct"]
        cm.cell(i, 7, f"={d[0]}*{cell[d[1]]}*{cell[d[2]]}" if d else 0)
        cm.cell(i, 8, f"=F{i}/60*{cell['owner_rate']}+G{i}")
        cm.cell(i, 9, f"=C{i}*E{i}")
        cm.cell(i, 10, f"=I{i}*H{i}")
        cm.cell(i, 11, f"=IF(SUM($J$2:$J${last})=0,0,J{i}/SUM($J$2:$J${last}))")
        cm.cell(i, 3).number_format = cm.cell(i, 11).number_format = "0%"
        for j in (7, 8, 10):
            cm.cell(i, j).number_format = '"$"#,##0.00'
        cm.cell(i, 9).number_format = "0.0"
        cm.cell(i, 6).fill = INPUT
    cm.cell(last + 1, 1, "Total").font = Font(bold=True)
    cm.cell(last + 1, 10, f"=SUM(J2:J{last})").number_format = '"$"#,##0'
    cm.cell(last + 1, 10).font = Font(bold=True)
    cm.cell(last + 3, 1, "Cost / incident = owner minutes × owner rate + direct cost (probability × cost from Assumptions). "
                         "Incidents / month = failure rate × monthly volume. Failure rates are measured; dollar inputs are assumptions.")
    cm.conditional_formatting.add(f"K2:K{last}", FormulaRule(formula=[f"K2=MAX($K$2:$K${last})"], fill=PatternFill("solid", fgColor="FDE2E1")))

    # ---------- Examples (every example linked to its trace and test case)
    exs = wb.create_sheet("Examples")
    rows = []
    for e in ex:
        failed = "; ".join(f"{c['name']}: {c['detail']}" for c in e["checks"] if not c["passed"])
        key = {k: v for k, v in e["metrics"].items() if isinstance(v, (int, float)) and k not in ("applicable",)}
        t = test_by_id.get(e["related_test"])
        rows.append([e["id"], e["sheet"], e["tag"], "pass" if e["passed"] else ("n/a" if not applicable_static(e) else "fail"),
                     ", ".join(e["modes"]), "; ".join(f"{k} {v}" for k, v in key.items()), failed,
                     e["ls"].get("latency_s") or e.get("latency_s"), e["ls"].get("total_tokens", 0),
                     ("link", trace_url(e), "trace"), e["related_test"], t["verdict"] if t else "",
                     ("link", test_url(t), "test trace") if t else ""])
    table(exs, ["ID", "Sheet", "Tag", "Result", "Failure modes", "Metrics", "Failed checks", "Latency s", "Tokens", "Golden trace",
                "Related test", "Test verdict", "Test trace"], rows, [7, 14, 12, 7, 22, 50, 70, 9, 8, 9, 10, 10, 10])
    exs.freeze_panes = "B2"
    exs.auto_filter.ref = exs.dimensions
    exs.conditional_formatting.add(f"D2:D{exs.max_row}", FormulaRule(formula=['$D2="fail"'], fill=PatternFill("solid", fgColor="FDE2E1")))
    exs.conditional_formatting.add(f"D2:D{exs.max_row}", FormulaRule(formula=['$D2="pass"'], fill=PatternFill("solid", fgColor="DCF5E3")))
    for path in paths:
        wb.save(path)


def applicable_static(e):
    return not (e["sheet"] == "Retrieval" and e["metrics"].get("applicable") == 0)


if __name__ == "__main__":
    print(build(sys.argv[1]))
