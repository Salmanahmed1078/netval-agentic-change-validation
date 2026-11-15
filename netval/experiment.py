"""Experiment runner (Phase 2, Step 7): paired design, resumable, parallel, fully logged."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from .model import Snapshot
from .twin import Twin
from .dsl import test_keys, key_covers
from .agent import AgenticValidator, AgentConfig, OneShotLLM
from .baselines import FullSuite, RuleBased, BatfishOnly
from .llm import get_llm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "data", "results")
INTENTS = ["clear", "vague", "terse"]
TUNING_TOPO = "campus"          # prompt-tuning split; test split = datacenter + isp


class Data:
    def __init__(self, path=os.path.join(ROOT, "data", "dataset.json")):
        d = json.load(open(path))
        self.bases = {k: Snapshot.from_json(v) for k, v in d["bases"].items()}
        self.cases = d["cases"]
        self.by_id = {c["id"]: c for c in self.cases}
        self._pre: Dict[str, Twin] = {}
        self._post: Dict[str, Twin] = {}
        self._lock = threading.Lock()

    def split(self, which: str) -> List[dict]:
        if which == "tune":
            return [c for c in self.cases if c["topology"] == TUNING_TOPO]
        if which == "test":
            return [c for c in self.cases if c["topology"] != TUNING_TOPO]
        return list(self.cases)

    def twins(self, c):
        with self._lock:
            if c["topology"] not in self._pre:
                self._pre[c["topology"]] = Twin(self.bases[c["topology"]])
            if c["id"] not in self._post:
                self._post[c["id"]] = Twin(Snapshot.from_json(c["post"]))
            return self._pre[c["topology"]], self._post[c["id"]]


def required_coverage(case, tests) -> float:
    req = [tuple(k) for k in case["required"]]
    if not req:
        return 1.0
    have = [k for t in tests for k in test_keys(t)]
    have += [("load",) + tuple(sorted((t.get("a", ""), t.get("b", "")))) for t in tests if t.get("t") == "load"]
    ok = 0
    for r in req:
        if r[0] == "mtu":
            ok += any(h[0] == "mtu" and h[1] == r[1] and h[2] == r[2] for h in have)
        elif r[0] == "path":
            ok += any((h[0] == "path" and h[1:3] == r[1:3]) for h in have)
        else:
            ok += any(key_covers(h, r) for h in have)
    return ok / len(req)


METHODS = {
    "B1_full_suite": lambda: FullSuite(),
    "B2_rule_based": lambda: RuleBased(),
    "B4_batfish_only": lambda: BatfishOnly(),
    "B3_oneshot_local": lambda: OneShotLLM(get_llm("local")),
    "B3_oneshot_hosted": lambda: OneShotLLM(get_llm("hosted")),
    "agent_local": lambda: AgenticValidator(get_llm("local")),
    "agent_hosted": lambda: AgenticValidator(get_llm("hosted")),
    "abl_no_impact_local": lambda: AgenticValidator(get_llm("local"), AgentConfig(use_impact=False)),
    "abl_no_feedback_local": lambda: AgenticValidator(get_llm("local"), AgentConfig(use_feedback=False)),
    "abl_no_invariants_local": lambda: AgenticValidator(get_llm("local"), AgentConfig(use_invariants=False)),
    "abl_tier1_only_local": lambda: AgenticValidator(get_llm("local"), AgentConfig(tiers=("t1",))),
    "abl_tier2_only_local": lambda: AgenticValidator(get_llm("local"), AgentConfig(tiers=("t2",))),
    "abl_no_impact_hosted": lambda: AgenticValidator(get_llm("hosted"), AgentConfig(use_impact=False)),
    "abl_no_feedback_hosted": lambda: AgenticValidator(get_llm("hosted"), AgentConfig(use_feedback=False)),
    "abl_no_invariants_hosted": lambda: AgenticValidator(get_llm("hosted"), AgentConfig(use_invariants=False)),
    "abl_tier1_only_hosted": lambda: AgenticValidator(get_llm("hosted"), AgentConfig(tiers=("t1",))),
    "abl_tier2_only_hosted": lambda: AgenticValidator(get_llm("hosted"), AgentConfig(tiers=("t2",))),
}
DETERMINISTIC = {"B1_full_suite", "B2_rule_based", "B4_batfish_only"}


def _done(path):
    seen = set()
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
                seen.add((r["case"], r["intent"], r["repeat"]))
            except Exception:
                pass
    return seen


def run(method: str, data: Data, cases: List[dict], intents=INTENTS, repeats=(1,), workers=4, log=print):
    os.makedirs(RES, exist_ok=True)
    path = os.path.join(RES, f"{method}.jsonl")
    done = _done(path)
    validator = METHODS[method]()
    if method in DETERMINISTIC:
        intents, repeats = ["clear"], (1,)
    jobs = [(c, i, r) for r in repeats for c in cases for i in intents if (c["id"], i, r) not in done]
    lock = threading.Lock()
    t0 = time.time()
    log(f"[{method}] {len(jobs)} runs to do ({len(done)} already done)")

    def one(job):
        c, intent, rep = job
        pre_t, post_t = data.twins(c)
        pre, post = data.bases[c["topology"]], post_t.s
        t1 = time.time()
        try:
            v = validator.validate(pre, post, c["tickets"][intent], seed=rep, tpre=pre_t, tpost=post_t)
        except Exception as e:  # keep going; record as tool error -> escalate
            from .agent import Verdict
            v = Verdict("escalate", f"pipeline error: {type(e).__name__}: {e}")
        wall = time.time() - t1
        rec = {"method": method, "case": c["id"], "topology": c["topology"], "cls": c["cls"], "variant": c["variant"],
               "template": c["template"], "label": c["label"], "intent": intent, "repeat": rep,
               "verdict": v.verdict, "reason": v.reason[:400], "n_tests": len(v.tests),
               "n_failed": sum(r.get("status") == "fail" for r in v.results),
               "n_errors": sum(r.get("status") == "error" for r in v.results),
               "tiers": sorted({r.get("tier") for r in v.results if r.get("tier")}),
               "tool_seconds": round(v.tool_seconds, 2), "llm_seconds": round(v.llm_seconds, 2),
               "ci_seconds": round(v.ci_seconds, 2), "wall_seconds": round(wall, 2),
               "llm_calls": v.llm_calls, "tokens_in": v.tokens_in, "tokens_out": v.tokens_out,
               "cost_usd": round(v.cost_usd, 5), "replans": v.replans, "impact_coverage": v.coverage,
               "required_coverage": round(required_coverage(c, v.tests), 3),
               "tests": v.tests[:80],
               "results": [{"test": r["test"], "status": r["status"], "tier": r.get("tier"), "detail": str(r.get("detail"))[:160]}
                           for r in v.results[:80]],
               "trace": v.trace}
        if method in DETERMINISTIC:
            rec["n_tests_total"] = len(v.tests)
            rec["tests"], rec["results"] = rec["tests"][:20], rec["results"][:20]
        with lock:
            with open(path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    n = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, j) for j in jobs]
        for f in as_completed(futs):
            r = f.result()
            n += 1
            if n % 10 == 0 or n == len(jobs):
                el = time.time() - t0
                log(f"[{method}] {n}/{len(jobs)} done, {el/60:.1f} min elapsed, ~{el/n*(len(jobs)-n)/60:.1f} min left "
                    f"(last: {r['case']} {r['intent']} -> {r['verdict']}, label {r['label']})")
    return path


def load(method: str) -> List[dict]:
    path = os.path.join(RES, f"{method}.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]
