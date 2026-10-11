"""Run the 200-case synthetic evaluation end to end and write eval_results/{results.json,report.md}.

    python scripts/run_eval.py                       # clean run
    python scripts/run_eval.py --failure-rate 0.05   # inject 5% transient failures to exercise retries
    COPILOT_PLANNER=openai_compat python scripts/run_eval.py   # evaluate a real LLM planner (e.g. Ollama)

Every case runs against a fresh copy of the mock Jira/CRM/Slack state. Sensitive steps are approved or
rejected by the case's designated approver (a real, authorized user) exactly as the API would do it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import ApprovalError, Copilot  # noqa: E402
from app.audit import Store  # noqa: E402
from app.config import Settings  # noqa: E402
from app.mocks import Backends  # noqa: E402
from app.models import RequestIn  # noqa: E402
from app.rag import Retriever, load_sops  # noqa: E402
from app.timing import manual_seconds  # noqa: E402

READ_TOOLS = {"directory_lookup", "crm_lookup_customer", "crm_lookup_order", "jira_search"}


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def acc(rows, key=lambda r: r["intent_ok"]):
    return (sum(key(r) for r in rows) / len(rows)) if rows else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--failure-rate", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "eval_results"))
    ap.add_argument("--cases", default=str(ROOT / "data" / "eval_cases.jsonl"))
    args = ap.parse_args()

    settings = Settings()
    settings.failure_rate = args.failure_rate
    tmp = tempfile.mkdtemp()
    settings.db_path = os.path.join(tmp, "eval.db")
    store = Store(settings.db_path)
    retriever = Retriever(load_sops(settings.sop_dir))
    cases = [json.loads(line) for line in open(args.cases)]

    rows = []
    for i, case in enumerate(cases):
        backends = Backends.from_data(settings.data_dir, failure_rate=args.failure_rate, seed=args.seed * 1000 + i)
        cop = Copilot(settings, backends, retriever, store)
        out = cop.resolve(RequestIn(requester_id=case["requester_id"], text=case["text"], channel="eval"))
        active, sim = out.latency_ms, out.sim_latency_ms
        approval_note = None
        if out.status == "awaiting_approval":
            try:
                out = cop.decide(out.approval_id, case["approver_id"], case["approval_decision"] or "approve", "eval harness")
                active, sim = out.latency_ms, out.sim_latency_ms
            except ApprovalError as e:
                approval_note = f"{e.status_code}: {e.message}"
        audit = store.audit_rows(out.request_id)
        lookups = sum(1 for a in audit if a["event"] == "tool_attempt" and a["tool"] in READ_TOOLS and a["status"] == "ok")
        executed = [s.tool for s in out.steps if s.status == "ok"]
        man = manual_seconds(executed, lookups, out.status)
        rows.append({
            "case_id": case["case_id"], "split": case["split"], "text": case["text"],
            "expected_intent": case["expected_intent"], "predicted_intent": out.intent,
            "intent_ok": out.intent == case["expected_intent"],
            "expected_status": case["expected_status"], "status": out.status, "status_ok": out.status == case["expected_status"],
            "failure_type": out.failure_type, "confidence": out.confidence, "retries": out.retries,
            "latency_ms": active, "sim_latency_ms": sim, "manual_s": man, "approval_note": approval_note, "note": case["note"],
        })

    # ------------------------------------------------------------------ aggregate
    dev = [r for r in rows if r["split"] == "dev"]
    test = [r for r in rows if r["split"] == "test"]
    sim = np.array([r["sim_latency_ms"] for r in rows]) / 1000.0
    live = np.array([r["latency_ms"] for r in rows]) / 1000.0
    man = np.array([r["manual_s"] for r in rows])
    exp_completed = [r for r in rows if r["expected_status"] == "completed"]
    per_intent = defaultdict(list)
    for r in rows:
        per_intent[r["expected_intent"]].append(r["intent_ok"])
    summary = {
        "settings": {"planner": settings.planner, "failure_rate": args.failure_rate, "seed": args.seed,
                     "min_score": settings.min_score, "min_margin": settings.min_margin, "cases": len(rows)},
        "intent_routing_accuracy": {"all": acc(rows), "dev": acc(dev), "test": acc(test)},
        "end_to_end_status_accuracy": {"all": acc(rows, lambda r: r["status_ok"]), "dev": acc(dev, lambda r: r["status_ok"]),
                                        "test": acc(test, lambda r: r["status_ok"])},
        "completion_rate_on_completable": acc(exp_completed, lambda r: r["status"] == "completed"),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "escalation_rate": sum(r["status"] == "escalated" for r in rows) / len(rows),
        "failure_types": dict(Counter(r["failure_type"] for r in rows if r["failure_type"])),
        "total_retries": int(sum(r["retries"] for r in rows)),
        "automated_measured_s": {"median": pct(live, 50), "p95": pct(live, 95)},
        "automated_simulated_s": {"median": pct(sim, 50), "p95": pct(sim, 95)},
        "manual_modelled_s": {"median": pct(man, 50), "p95": pct(man, 95)},
        "median_time_reduction_pct": 100 * (1 - pct(sim, 50) / pct(man, 50)),
        "per_intent_routing_accuracy": {k: sum(v) / len(v) for k, v in sorted(per_intent.items())},
        "audit_chain": store.verify_chain(),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    tag = "" if args.failure_rate == 0 else f"_fail{int(args.failure_rate * 100)}"
    (out_dir / f"results{tag}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1))

    wrong_intent = [r for r in rows if not r["intent_ok"]]
    wrong_status = [r for r in rows if not r["status_ok"]]
    s = summary
    md = [f"# Evaluation report{' (failure injection ' + str(args.failure_rate) + ')' if tag else ''}", "",
          f"Planner `{settings.planner}` - {len(rows)} synthetic cases - failure rate {args.failure_rate} - seed {args.seed}", "",
          "| Metric | All | Dev (80) | Test (120) |", "|---|---|---|---|",
          f"| Intent-routing accuracy | {s['intent_routing_accuracy']['all']:.1%} | {s['intent_routing_accuracy']['dev']:.1%} | {s['intent_routing_accuracy']['test']:.1%} |",
          f"| End-to-end outcome accuracy | {s['end_to_end_status_accuracy']['all']:.1%} | {s['end_to_end_status_accuracy']['dev']:.1%} | {s['end_to_end_status_accuracy']['test']:.1%} |", "",
          f"* Completion rate on completable requests: **{s['completion_rate_on_completable']:.1%}**",
          f"* Final statuses: {s['status_counts']}  |  escalation rate {s['escalation_rate']:.1%}",
          f"* Failure types: {s['failure_types']}  |  total retries {s['total_retries']}",
          f"* Audit chain valid: {s['audit_chain']['valid']} ({s['audit_chain']['rows']} rows)", "",
          "## Handling time (excludes human approval wait on both sides)", "",
          "| | Median | p95 |", "|---|---|---|",
          f"| Automated, measured compute only | {s['automated_measured_s']['median']:.3f}s | {s['automated_measured_s']['p95']:.3f}s |",
          f"| Automated, simulated (compute + virtual API latency) | {s['automated_simulated_s']['median']:.2f}s | {s['automated_simulated_s']['p95']:.2f}s |",
          f"| Manual, MODELLED (assumed step times in app/timing.py) | {s['manual_modelled_s']['median']:.0f}s | {s['manual_modelled_s']['p95']:.0f}s |", "",
          f"Median reduction vs modelled manual baseline: **{s['median_time_reduction_pct']:.1f}%**", "",
          "## Per-intent routing accuracy", "", "| Intent | Accuracy |", "|---|---|"]
    md += [f"| {k} | {v:.0%} |" for k, v in s["per_intent_routing_accuracy"].items()]
    md += ["", f"## Misrouted cases ({len(wrong_intent)})", ""]
    md += [f"* `{r['case_id']}` [{r['split']}] expected **{r['expected_intent']}**, got **{r['predicted_intent']}** (conf {r['confidence']}): {r['text']}" for r in wrong_intent]
    md += ["", f"## Cases with wrong final outcome ({len(wrong_status)})", ""]
    md += [f"* `{r['case_id']}` [{r['split']}] expected {r['expected_status']}, got {r['status']} ({r['failure_type']}): {r['text']}" for r in wrong_status]
    (out_dir / f"report{tag}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:24]))
    print(f"\n... full report: {out_dir / ('report' + tag + '.md')}")


if __name__ == "__main__":
    main()
