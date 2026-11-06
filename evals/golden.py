"""Evaluate FixIT-Pro_golden_dataset.xlsx as a LangSmith experiment.

    python3 -m evals.golden --project-link fixit-local          # all 118 examples (~30 min, mostly local Qwen)
    python3 -m evals.golden --sheets Retrieval,Ranking           # fast subset

1. Syncs the spreadsheet to the LangSmith dataset "fixit-pro-golden" (one example per row; split = sheet; metadata:
   golden_id, sheet, tag, related_test, confidence).
2. Runs every example through the real app code (Pinecone search, Qwen, agents) with client.evaluate, so each
   example is one traced run with its LLM/retriever/tool children, plus feedback for every metric, `pass`, and
   `failure_modes`.
3. Writes evals/results/golden-<run>/golden_results.json, then evals.golden_report builds the analysis workbook.

Checks are deterministic and documented next to each sheet below. Failed checks are labeled with a failure mode
(see MODES in golden_report.py); attribution signals (rule urgency, score spread, retrieved IDs) are recorded so
failures can be traced to their cause."""
import argparse, json, os, re, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

from . import harness

GOLDEN = harness.ROOT / "FixIT-Pro_golden_dataset.xlsx"
DATASET = "fixit-pro-golden"
URGENCY = ["low", "normal", "high", "emergency"]


# ======================================================================= loading the spreadsheet

def _ids(cell):
    return re.findall(r"^(\d{3}) ", cell or "", re.M)


def _list(cell):
    return [x.strip() for x in str(cell or "").split(";") if x.strip()]


def _fields(cell):
    out = {}
    for line in str(cell or "").splitlines():
        k, _, v = line.partition(": ")
        out[k] = v
    return out


# Structured inputs for the Messages sheet (the sheet's "Other inputs" column is the human-readable version).
SLOTS = ["Mon Nov 9, 9-11am", "Tue Nov 10, 1-3pm"]
MSG_INPUTS = {
    "M01": {"agent": "pricing", "media": 0},
    "M02": {"agent": "pricing", "media": 0},
    "M03": {"agent": "pricing", "media": (2, 1)},
    "M04": {"agent": "pricing", "media": 0},
    "M05": {"agent": "confirm", "name": "Real Handy Handyman", "quote": "$180.00 (parts included)", "slots": SLOTS},
    "M06": {"agent": "confirm", "name": "Real Handy Handyman", "quote": "$180.00 (parts included)", "slots": SLOTS,
            "feedback": "Mention that parking is on the street"},
    "M07": {"agent": "confirm", "name": "Anytime Handyman", "quote": "price on request",
            "slots": ["Today 6-8pm", "Tomorrow 8-10am", "Tomorrow 12-2pm"]},
    "M08": {"agent": "confirm", "name": "Keegan West", "quote": "$650.00", "tenant": "",
            "slots": ["Sat Nov 14, 10am-2pm", "Sun Nov 15, 10am-2pm"]},
}


def load_golden(path=GOLDEN):
    import openpyxl
    wb = openpyxl.load_workbook(path)
    rows = lambda name: [dict(zip([c.value for c in wb[name][1]], [c.value for c in r]))
                         for r in wb[name].iter_rows(min_row=2)]
    ex = []
    for r in rows("Retrieval"):
        ex.append({"id": r["ID"], "sheet": "Retrieval", "tag": r["Tag"], "related_test": r["Related test"] or "",
                   "confidence": r["Label confidence"],
                   "inputs": {"query": r["Query"], "location": r["Location"]},
                   "reference": {"grade2": _ids(r["Highly relevant (grade 2)"]), "grade1": _ids(r["Partially relevant (grade 1)"]),
                                 "avoid": _ids(r["Should not rank top 3"])}})
    for r in rows("Translate"):
        ex.append({"id": r["ID"], "sheet": "Translate", "tag": r["Tag"], "related_test": r["Related test"] or "",
                   "confidence": r["Label confidence"],
                   "inputs": {"request": r["Tenant request"], "location": r["Location"]},
                   "reference": {"category": r["Expected category"], "categories": _list(r["Acceptable categories"]),
                                 "urgency": r["Expected urgency"], "urgencies": _list(r["Acceptable urgencies"]),
                                 "must_mention": _list(r["Description must mention"]), "must_not": _list(r["Description must not contain"])}})
    for r in rows("Ranking"):
        ex.append({"id": r["ID"], "sheet": "Ranking", "tag": r["Tag"], "related_test": r["Related test"] or "",
                   "confidence": "High", "inputs": {"criterion": r["Criterion"], "fields": _fields(r["Input fields"]),
                                                    "source_profile": r["Source profile"]},
                   "reference": {"expected": float(r["Expected score"])}})
    for r in rows("Recommendation"):
        ex.append({"id": r["ID"], "sheet": "Recommendation", "tag": r["Tag"], "related_test": r["Related test"] or "",
                   "confidence": r["Label confidence"], "inputs": {"question": r["Question"], "location": r["Location"]},
                   "reference": {"acceptable": _ids(r["Acceptable sources"]), "also": _ids(r["Also acceptable"]),
                                 "must_include": r["Must include"], "caveats": r["Required caveats (from profiles)"],
                                 "must_not": r["Must not"]}})
    for r in rows("Messages"):
        ex.append({"id": r["ID"], "sheet": "Messages", "tag": r["Tag"], "related_test": "", "confidence": "High",
                   "inputs": {"service_request": _fields(r["Service request"]), **MSG_INPUTS[r["ID"]]},
                   "reference": {"must_include": r["Must include"], "must_not": r["Must not include"], "length": r["Length"]}})
    return ex


# ======================================================================= running examples (the system under test)

class Runner:
    def __init__(self):
        import agents, media, rag
        self.agents, self.media, self.rag = agents, media, rag
        self.profiles = {p["ID"]: p for p in json.loads((harness.ROOT / "santa_clara_home_repair_profiles.json").read_text())}

    def __call__(self, inputs):
        from langsmith.run_helpers import get_current_run_tree
        run = get_current_run_tree()
        if run:  # golden metadata on the target run, so traces can be filtered by golden_id / related_test
            run.set(metadata={k: inputs[k] for k in ("golden_id", "sheet", "tag", "related_test")},
                    tags=["fixit-pro-golden", inputs["sheet"].lower(), inputs["tag"]])
        return getattr(self, inputs["sheet"].lower())(inputs)

    def retrieval(self, i):
        matches = self.rag.search(f"{i['query']} in {i['location']}", top_k=10)
        ids = [m["id"].removeprefix("technician-") for m in matches]
        scores = [round(m["score"], 4) for m in matches]
        return {"top3": ids[:3], "top10": ids, "scores": scores, "spread": round(scores[0] - scores[-1], 4) if scores else 0}

    def translate(self, i):
        out = self.agents.translate_agent({"request_text": i["request"], "location": i["location"]})
        log = out["log"][0]["text"]
        return {"service_request": out["service_request"], "how": "Qwen" if "via Qwen" in log else "keyword rules",
                "rule_urgency": self.agents.rule_urgency(i["request"]), "log": log}

    def ranking(self, i):
        fn = {"License": self.agents.license_score, "Reviews": self.agents.review_score, "Cost": self.agents.cost_score}[i["criterion"]]
        score, reason = fn(i["fields"])
        return {"score": round(score, 4), "reason": reason}

    def recommendation(self, i):
        question = f"{i['question']} in {i['location']}"
        matches = self.rag.search(question)
        answer = self.rag.answer(question, matches)
        return {"answer": answer, "retrieved": [m["id"].removeprefix("technician-") for m in matches],
                "sources": [m["title"] for m in matches]}

    def messages(self, i):
        sr = {**i["service_request"], "search_query": i["service_request"].get("title", "")}
        if i["agent"] == "pricing":
            photos, videos = i["media"] if i["media"] else (0, 0)
            fake = [{"kind": "image"}] * photos + [{"kind": "video"}] * videos
            cand = {"id": "technician-005", "name": "Real Handy Handyman", "fields": self.profiles["005"], "website": None}
            old = self.media.list_media
            self.media.list_media = lambda job_id: fake
            try:
                out = self.agents.pricing_agent({"service_request": sr, "shortlist": [cand]}, {"configurable": {"thread_id": "0" * 32}})
            finally:
                self.media.list_media = old
            return {"message": out["quote_requests"][0]["message"], "log": out["log"][0]["text"]}
        state = {"service_request": sr, "selected": {"name": i["name"], "quote_text": i["quote"]},
                 "tenant_name": i.get("tenant", "Eval tenant"), "timeslots": i["slots"], "message_feedback": i.get("feedback", "")}
        out = self.agents.job_confirmation_agent(state)
        return {"message": out["confirmation_message"], "log": out["log"][0]["text"]}


# ======================================================================= scoring

class Scorer:
    """check(name, ok, mode, detail): a failed check is labeled with a failure mode."""

    def __init__(self):
        self.metrics, self.checks = {}, []

    def check(self, name, ok, mode, detail=""):
        self.checks.append({"name": name, "passed": bool(ok), "mode": None if ok else mode, "detail": str(detail)[:300]})
        return bool(ok)

    def metric(self, name, value):
        self.metrics[name] = value

    def result(self):
        modes = sorted({c["mode"] for c in self.checks if c["mode"]})
        return {"metrics": self.metrics, "checks": self.checks, "passed": not modes, "modes": modes}


def _stem_in(word, text):
    """Stem found anywhere in the text, so 'caulk' matches 'recaulking' and 'drip' matches 'dripping'."""
    return word.lower()[:max(3, min(5, len(word) - 1))] in text


def _mentions(item, text):
    """'faucet (grifo)' → faucet OR grifo; each alternative needs all its words (prefix match)."""
    alts = [a.strip() for a in re.split(r"[()]", item) if a.strip()]
    return any(all(_stem_in(w, text) for w in re.findall(r"[a-z0-9']{3,}", a.lower())) for a in alts)


def score_retrieval(ex, out, P):
    s, ref = Scorer(), ex["reference"]
    g2, g1, top = set(ref["grade2"]), set(ref["grade1"]), out["top3"]
    if not g2 and not g1:  # negative example: retrieval can't abstain; judged on the Recommendation sheet (A07)
        s.metric("applicable", 0)
        return s.result()
    gains = [2 if t in g2 else 1 if t in g1 else 0 for t in top]
    ideal = sorted([2] * len(g2) + [1] * len(g1), reverse=True)[:3]
    dcg = lambda g: sum(v / __import__("math").log2(i + 2) for i, v in enumerate(g))
    hit = any(gains)
    s.metric("hit@3", int(hit))
    s.metric("precision@3", round(sum(1 for g in gains if g) / 3, 3))
    s.metric("ndcg@3", round(dcg(gains) / dcg(ideal), 3) if ideal else None)
    first = next((n + 1 for n, t in enumerate(out["top10"]) if t in g2 | g1), None)
    s.metric("first_relevant_rank", first)
    s.metric("score_spread_top10", out["spread"])
    s.check("a relevant technician in the top 3", hit, "RET-MISS",
            f"top3 {top}; first relevant at rank {first}; top-10 spread {out['spread']}")
    intruders = [t for t in top if t in ref["avoid"]]
    s.check("no should-not technician in the top 3", not intruders, "RET-INTRUDER", intruders)
    return s.result()


def score_translate(ex, out, P):
    s, ref, sr = Scorer(), ex["reference"], out["service_request"]
    text = f"{sr['title']} {sr['description']}".lower()
    cat_ok = sr["category"] in ref["categories"]
    s.metric("category_exact", int(sr["category"] == ref["category"]))
    s.metric("category_acceptable", int(cat_ok))
    s.check("category acceptable", cat_ok, "TR-CAT" if out["how"] == "Qwen" else "TR-CAT-RULES",
            f"got {sr['category']}, want {ref['categories']}")
    got, ok_u = sr["urgency"], ref["urgencies"]
    s.metric("urgency_exact", int(got == ref["urgency"]))
    s.metric("urgency_acceptable", int(got in ok_u))
    if got not in ok_u:
        over = URGENCY.index(got) > max(URGENCY.index(u) for u in ok_u)
        floor_forced = out["rule_urgency"] == got and out["rule_urgency"] not in ok_u
        mode = ("TR-URG-FLOOR" if floor_forced else "TR-URG-OVER") if over else "TR-URG-UNDER"
        s.check("urgency acceptable", False, mode, f"got {got}, want {ok_u}; keyword floor {out['rule_urgency']}; via {out['how']}")
    else:
        s.check("urgency acceptable", True, None)
    found = [m for m in ref["must_mention"] if _mentions(m, text)]
    s.metric("fact_coverage", round(len(found) / len(ref["must_mention"]), 3) if ref["must_mention"] else None)
    s.check("description keeps the tenant's facts", len(found) == len(ref["must_mention"]), "TR-FACT",
            f"missing {[m for m in ref['must_mention'] if m not in found]} in: {sr['description'][:160]}")
    leaked = [m for m in ref["must_not"] if m.lower() in text]
    s.check("no injected or invented content", not leaked, "TR-LEAK", leaked)
    s.metric("via_qwen", int(out["how"] == "Qwen"))
    return s.result()


def score_ranking(ex, out, P):
    s = Scorer()
    exp, got = ex["reference"]["expected"], out["score"]
    s.metric("abs_error", round(abs(got - exp), 4))
    mode = {"Cost": "RANK-FEE-PARSE", "License": "RANK-LICENSE-RULE", "Reviews": "RANK-REVIEW"}[ex["inputs"]["criterion"]]
    s.check("score matches the spec", abs(got - exp) < 0.001, mode, f"got {got} ({out['reason']}), spec {exp}")
    return s.result()


def _named(answer, P):
    """IDs of profiles the answer names (by full name, or distinctive words for 'A / B' names)."""
    import agents
    text = answer
    hits = []
    for i, p in P.items():
        name = p["Name / company"]
        if name.lower() in text.lower() or agents.name_matches(name, text):
            hits.append(i)
    # 'RC HANDYMAN SERVICES' also matches 'RC HANDYMAN Interior…'; keep the longer exact-name hit
    exact = {i for i in hits if P[i]["Name / company"].lower() in text.lower()}
    return sorted(exact or hits)


REC_RULES = {  # golden_id: (must include, caveat, must not) as regexes; None = not applicable
    "A01": (r"not verified|not published", None, r"\b(is|are) (fully )?licensed\b|free (repair|service)\b"),  # caveat set below
    "A02": (r"not published", None, None),
    "A03": (r"1131330|866058", None, r"\b(001|proserv)[^.\n]*\bis licensed\b"),
    "A04": (r"24/7|24-hour|24 hours", r"confirm|verify|check|contact", r"\b(is|are) available (tonight|now)\b"),
    "A05": (r"not published", r"free estimate", r"\$\s?\d"),
    "A06": (r"conflict", None, r"\bis (a )?licensed\b(?![^.]*conflict)|\bis unlicensed\b"),
    "A07": (r"no (suitable |matching )?(provider|technician|profile|match)|not (a )?(fit|match)|none of|doesn.t (fit|match|offer)|do not (offer|list)|cannot|can't", None, None),
    "A08": (r"r&r california", r"unavailable|not available|currently", r"verified (class b|contractor)"),
    "A09": (r"\$150", r"not published", None),
    "A10": (None, None, r"best plumbing co|555-0000"),
    "A11": (None, None, None),
    "A12": (r"1148145|866058", r"not verified|verify|registry|confirm", r"registry status (is )?verified|verified (license|registry)"),
}


def score_recommendation(ex, out, P):
    s, ref, ans = Scorer(), ex["reference"], out["answer"]
    low = ans.lower()
    ok_ids = set(ref["acceptable"]) | set(ref["also"])
    named = _named(ans, P)
    retrieved_ok = [i for i in out["retrieved"] if i in ok_ids]
    s.metric("named_providers", len(named))
    negative = not ok_ids
    upstream = bool(ok_ids) and not retrieved_ok  # retrieval never offered an acceptable provider to the model
    if negative:
        rule = REC_RULES[ex["id"]][0]
        s.check("says no provider fits", re.search(rule, low), "REC-ABSTAIN", ans[:200])
    else:
        good = [i for i in named if i in ok_ids]
        s.metric("provider_precision", round(len(good) / len(named), 3) if named else 0)
        s.check("recommends an acceptable provider", good, "REC-UPSTREAM" if upstream else "REC-OFFLIST",
                f"named {named}; retrieved {out['retrieved']}; acceptable {sorted(ok_ids)}")
        bad = [i for i in named if i not in ok_ids]
        s.check("recommends no unacceptable provider", not bad, "REC-UPSTREAM" if upstream else "REC-OFFLIST", bad)
    # grounding: every phone/email/$ in the answer exists in the retrieved profiles
    src = " ".join(str(v) for i in out["retrieved"] if i in P for v in P[i].values())
    digits = re.sub(r"\D", "", src)
    phones = [p for p in re.findall(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", ans) if re.sub(r"\D", "", p) not in digits]
    emails = [e for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", ans) if e.lower().rstrip(".") not in src.lower()]
    prices = [p for p in re.findall(r"\$\s?\d[\d,]*", ans) if p.replace(" ", "") not in src.replace(" ", "")]
    s.check("contacts and prices come from the profiles", not (phones or emails or prices), "REC-UNGROUNDED",
            f"phones {phones} emails {emails} prices {prices}")
    include, caveat, must_not = REC_RULES[ex["id"]]
    if include and not negative:
        s.check("includes the required facts", re.search(include, low), "REC-OMIT", f"/{include}/")
    if ex["id"] == "A01":  # caveat only applies when 001 is recommended
        caveat = r"conflict" if "001" in named else None
    if ex["id"] == "A11" and "027" in named:
        caveat = r"cupertino|mountain view|sunnyvale|saratoga|west san jose|not (listed|serve)"
    if caveat:
        s.check("states the required caveat", re.search(caveat, low), "REC-CAVEAT", f"/{caveat}/")
    if must_not:
        m = re.search(must_not, low)
        s.check("makes no forbidden claim", not m, "REC-INJECTION" if ex["tag"] == "adversarial" else "REC-CLAIM", m and m.group())
    # Any provider section that calls an unlicensed-on-record provider licensed or verified (profiles with no license number)
    unsupported = []
    for section in re.split(r"(?=\[\d\])", ans):
        sec = section.lower()
        claim = re.search(r"(?<!not )(?<!no )\b(is|are|fully|properly)?\s?licensed\b|(?<!not )(?<!un)\bverified\b(?! (license|status) (is )?not)", sec)
        if not claim or re.search(r"not (licensed|verified)|unverified|conflict", sec[max(0, claim.start() - 40): claim.end() + 40]):
            continue
        for i in _named(section, P):
            if not re.search(r"\d{5,}", P[i].get("License number") or ""):
                unsupported.append(f"{i}: '{claim.group().strip()}'")
    s.check("no license claim the profile doesn't support", not unsupported, "REC-CLAIM", unsupported)
    if ex["id"] == "A02":
        missing = [i for i in named if self_phone(P, i) and self_phone(P, i) not in re.sub(r"\D", "", ans)]
        s.check("phone exactly as in the profile", not missing, "REC-OMIT", missing)
    s.metric("upstream_retrieval_miss", int(upstream))
    return s.result()


def self_phone(P, i):
    m = re.search(r"\d[\d().\-\s]{8,}\d", P[i].get("Phone") or "")
    return re.sub(r"\D", "", m.group()) if m else ""


DATE = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}\b|\b\d{1,2}/\d{1,2}\b"


def score_messages(ex, out, P):
    s, i, msg = Scorer(), ex["inputs"], out["message"]
    low, sr = msg.lower(), i["service_request"]
    s.metric("words", len(msg.split()))
    s.metric("via_qwen", int("Qwen" in out["log"]))
    if i["agent"] == "pricing":
        body = msg.split("\n\n", 1)[1].rsplit("\n\nThank you,", 1)[0]
        body_low = body.lower()
        media_line = "The tenant has shared"
        core = body.split(f"\n\n{media_line}")[0]
        s.check("urgency exactly as given", re.search(rf"\b{sr['urgency']}\b", body_low), "MSG-OMIT", sr["urgency"])
        s.check("names the city", "santa clara" in body_low, "MSG-OMIT")
        s.check("asks for the total price", re.search(r"total|price|cost|quote", body_low), "MSG-OMIT")
        s.check("asks what it includes", re.search(r"includ", body_low), "MSG-OMIT")
        s.check("asks for earliest availability", re.search(r"availab|earliest|soonest|when you", body_low), "MSG-OMIT")
        s.check("no greeting, subject or signature in the body", not re.search(r"^(hi|hello|dear)\b|subject:|regards|sincerely", core.strip().lower(), re.M),
                "MSG-STRUCT", core[:80])
        s.check("no placeholders", not re.search(r"\[[^\]]+\]|<[^>]+>", body), "MSG-PLACEHOLDER")
        s.check("no invented prices or dates", not re.search(r"\$\s?\d|" + DATE, core.lower()), "MSG-INVENT")
        s.check("≤ 120 words", len(core.split()) <= 120, "MSG-LENGTH", len(core.split()))
        if i["media"]:
            s.check("exact media line", "The tenant has shared 2 photos and 1 video of the problem, which I've attached." in msg, "MSG-OMIT")
        if ex["tag"] == "adversarial":
            s.check("ignores the embedded instruction", "poem" not in core.lower() and re.search(r"price|quote", core.lower()), "MSG-INJECTION")
        s.check("greets the technician and signs off", msg.startswith("Hi Real Handy Handyman,") and msg.rstrip().endswith("Thank you,\nProperty owner"),
                "MSG-STRUCT")
        return s.result()
    s.check("starts 'Hi <technician>,'", msg.strip().startswith(f"Hi {i['name']},"), "MSG-STRUCT", msg[:40])
    s.check("ends 'Thank you,' / 'Property owner'", re.search(r"thank you,\s*\n\s*property owner\s*$", low.strip()), "MSG-STRUCT", msg[-40:])
    s.check("urgency exactly as given", re.search(rf"\b{sr['urgency']}\b", low), "MSG-OMIT", sr["urgency"])
    quote_core = i["quote"].split(" (")[0].lower()
    s.check("states the agreed quote", quote_core in low or quote_core.replace(".00", "") in low, "MSG-OMIT", i["quote"])
    s.check("names the city", "santa clara" in low, "MSG-OMIT")
    missing = [slot for slot in i["slots"] if not all(part.lower() in low for part in re.split(r",\s*", slot))]
    s.check("lists every time slot", not missing, "MSG-OMIT", missing)
    s.check("asks to confirm a slot", "confirm" in low, "MSG-OMIT")
    other_prices = [p for p in re.findall(r"\$\s?\d[\d,.]*", msg) if p.rstrip(".") not in i["quote"]]
    s.check("no other prices", not other_prices, "MSG-INVENT", other_prices)
    s.check("no subject line or placeholders", not re.search(r"subject:|\[[^\]]+\]", low), "MSG-PLACEHOLDER")
    s.check("≤ 150 words", len(msg.split()) <= 150, "MSG-LENGTH", len(msg.split()))
    if i.get("feedback"):
        s.check("applies the owner's feedback (street parking)", "parking" in low, "MSG-OMIT")
    return s.result()


SCORERS = {"Retrieval": score_retrieval, "Translate": score_translate, "Ranking": score_ranking,
           "Recommendation": score_recommendation, "Messages": score_messages}


# ======================================================================= LangSmith dataset + experiment

def sync_dataset(client, examples):
    """Make the LangSmith dataset match the spreadsheet (create, update or delete by golden_id)."""
    try:
        ds = client.read_dataset(dataset_name=DATASET)
    except Exception:
        ds = client.create_dataset(DATASET, description="FixIT-Pro golden dataset (FixIT-Pro_golden_dataset.xlsx). "
                                                        "Split = sheet; metadata.golden_id = spreadsheet row ID.")
    existing = {(e.metadata or {}).get("golden_id"): e for e in client.list_examples(dataset_id=ds.id)}
    new, changed = [], 0
    for e in examples:
        inputs = {**e["inputs"], "golden_id": e["id"], "sheet": e["sheet"], "tag": e["tag"], "related_test": e["related_test"]}
        meta = {"golden_id": e["id"], "sheet": e["sheet"], "tag": e["tag"], "related_test": e["related_test"], "confidence": e["confidence"]}
        old = existing.pop(e["id"], None)
        if old is None:
            new.append({"inputs": inputs, "outputs": e["reference"], "metadata": meta, "split": e["sheet"].lower()})
        elif old.inputs != inputs or old.outputs != e["reference"] or (old.metadata or {}).get("tag") != e["tag"]:
            client.update_example(old.id, inputs=inputs, outputs=e["reference"], metadata=meta, split=e["sheet"].lower())
            changed += 1
    if new:
        client.create_examples(dataset_id=ds.id, examples=new)
    for stale in existing.values():  # rows removed from the spreadsheet
        client.delete_example(stale.id)
    return ds, {"created": len(new), "updated": changed, "deleted": len(existing)}


FEEDBACK_KEYS = {"pass", "failure_modes", "hit@3", "precision@3", "ndcg@3", "first_relevant_rank", "score_spread_top10",
                 "category_exact", "category_acceptable", "urgency_exact", "urgency_acceptable", "fact_coverage", "via_qwen",
                 "abs_error", "named_providers", "provider_precision", "upstream_retrieval_miss", "words"}


def feedback_of(res):
    out = [{"key": "pass", "score": int(res["passed"]),
            "comment": "; ".join(f"{c['name']}: {c['detail']}" for c in res["checks"] if not c["passed"])[:1000] or "all checks passed"},
           {"key": "failure_modes", "value": ",".join(res["modes"]) or "none"}]
    return out + [{"key": k, "score": v} for k, v in res["metrics"].items() if isinstance(v, (int, float))]


def rescore(folder):
    """Re-score saved outputs with the current scorers and golden labels, and replace the experiment's feedback in
    LangSmith (no model calls). Use after fixing a scorer or a label; the change log is in the golden dataset's About sheet."""
    import warnings
    warnings.filterwarnings("ignore")
    from dotenv import load_dotenv
    from langsmith import Client
    load_dotenv(harness.ROOT / ".env", override=True)
    client = Client()
    folder = Path(folder)
    payload = json.loads((folder / "golden_results.json").read_text())
    golden = {e["id"]: e for e in load_golden()}
    ds, sync = sync_dataset(client, list(golden.values()))
    P = {p["ID"]: p for p in json.loads((harness.ROOT / "santa_clara_home_repair_profiles.json").read_text())}
    old = list(client.list_feedback(run_ids=[e["run_id"] for e in payload["examples"]]))
    for f in {f.id: f for f in old}.values():
        if f.key in FEEDBACK_KEYS:
            try:
                client.delete_feedback(f.id)
            except Exception as err:  # already gone (listing can repeat items); anything else is a real error
                if "not found" not in str(err).lower():
                    raise
    changed = 0
    for e in payload["examples"]:
        g = golden[e["id"]]
        e["reference"], e["inputs"] = g["reference"], g["inputs"] | {k: v for k, v in e["inputs"].items() if k not in g["inputs"]}
        res = SCORERS[e["sheet"]](g, e["outputs"], P) if e.get("outputs") else {"metrics": {}, "checks": e["checks"], "passed": False, "modes": ["ERROR"]}
        changed += res["passed"] != e["passed"] or res["modes"] != e["modes"]
        e.update(res)
        for fb in feedback_of(res):
            client.create_feedback(e["run_id"], trace_id=e["trace_id"], session_id=payload["experiment_id"], **fb)
    payload["rescored"] = datetime.now(timezone.utc).isoformat()
    (folder / "golden_results.json").write_text(json.dumps(payload, indent=1, default=str))
    from .golden_report import build
    return f"Rescored {len(payload['examples'])} examples ({changed} changed; dataset {sync}); replaced {len(old)} feedback items.\n" + build(folder)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rescore", metavar="RESULTS_DIR", help="re-score saved outputs and replace the experiment's feedback")
    ap.add_argument("--sheets", help="comma-separated sheets (default: all)")
    ap.add_argument("--project-link", default="fixit-local", help="LangSmith project of the test-case run to link")
    args = ap.parse_args(argv)
    if args.rescore:
        harness.configure(Path(args.rescore) / "work")
        print(rescore(args.rescore))
        return

    run_id = "golden-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = harness.ROOT / "evals" / "results" / run_id
    harness.configure(out_dir / "work")
    import rag  # noqa: E402  (.env: LANGSMITH_API_KEY, Pinecone)
    from langsmith import Client
    rag.namespace = harness.EVAL_NAMESPACE
    if not os.getenv("LANGSMITH_API_KEY"):
        sys.exit("LANGSMITH_API_KEY isn't set in FixIT-Pro/.env.")
    client = Client()

    examples = load_golden()
    sheets = set(args.sheets.split(",")) if args.sheets else None
    ds, sync = sync_dataset(client, examples)
    print(f"Dataset {DATASET}: {len(examples)} examples ({sync})")
    by_id = {e["id"]: e for e in examples}
    P = {p["ID"]: p for p in json.loads((harness.ROOT / "santa_clara_home_repair_profiles.json").read_text())}
    scored = {}

    def evaluator(run, example):
        gid = example.metadata["golden_id"]
        ex = by_id[gid]
        if run.error or not run.outputs:
            res = {"metrics": {}, "checks": [{"name": "target ran", "passed": False, "mode": "ERROR", "detail": run.error}],
                   "passed": False, "modes": ["ERROR"]}
        else:
            res = SCORERS[ex["sheet"]](ex, run.outputs, P)
        scored[gid] = {**res, "run_id": str(run.id), "trace_id": str(run.trace_id),
                       "latency_s": round((run.end_time - run.start_time).total_seconds(), 2) if run.end_time else None}
        return {"results": feedback_of(res)}

    data = [e for e in client.list_examples(dataset_id=ds.id)
            if not sheets or (e.metadata or {}).get("sheet") in sheets]
    data.sort(key=lambda e: e.metadata["golden_id"])
    meta = {"eval_run_id": run_id, "model": rag.model_name(), "pinecone_namespace": rag.namespace, "dataset_rows": len(data)}
    try:
        import subprocess
        meta["git_sha"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=harness.ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        pass
    started = time.time()
    results = client.evaluate(Runner(), data=data, evaluators=[evaluator], experiment_prefix="golden",
                              description="Golden dataset evaluation (evals/golden.py)", metadata=meta, max_concurrency=0)
    rows = list(results)  # blocks until every example has run
    experiment = results.experiment_name
    session = client.read_project(project_name=experiment)
    print(f"Experiment {experiment}: {len(rows)} runs in {time.time() - started:.0f}s")
    if rag._llm is not None:
        rag._llm.close()
        rag._llm = None

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"eval_run_id": run_id, "experiment": experiment, "experiment_id": str(session.id), "dataset_id": str(ds.id),
               "tenant_id": str(session.tenant_id), "project_link": args.project_link, "metadata": meta,
               "finished": datetime.now(timezone.utc).isoformat(), "examples": []}
    for e in examples:
        if e["id"] in scored:
            payload["examples"].append({**{k: e[k] for k in ("id", "sheet", "tag", "related_test", "confidence", "inputs", "reference")},
                                        **scored[e["id"]], "outputs": next((r["run"].outputs for r in rows
                                                                            if r["example"].metadata["golden_id"] == e["id"]), None)})
    (out_dir / "golden_results.json").write_text(json.dumps(payload, indent=1, default=str))
    print(f"Results: {out_dir / 'golden_results.json'}")
    from .golden_report import build
    print(build(out_dir))


if __name__ == "__main__":
    main()
