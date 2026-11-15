"""Metrics and statistics (Phase 2, Steps 8-9)."""
from __future__ import annotations

import math
from collections import defaultdict, Counter
from typing import Dict, List

import numpy as np
from scipy import stats


def majority(vs: List[str]) -> str:
    c = Counter(vs).most_common()
    if len(c) > 1 and c[0][1] == c[1][1]:      # tie -> the safer verdict
        for v in ("block", "escalate", "approve"):
            if v in (c[0][0], c[1][0]):
                return v
    return c[0][0]


def collapse(recs: List[dict], mode="first") -> List[dict]:
    """One record per (case, intent). mode=first -> repeat 1 ; mode=majority -> majority verdict over repeats."""
    g = defaultdict(list)
    for r in recs:
        g[(r["case"], r["intent"])].append(r)
    out = []
    for k, rs in g.items():
        rs = sorted(rs, key=lambda r: r["repeat"])
        base = dict(rs[0])
        if mode == "majority" and len(rs) > 1:
            base["verdict"] = majority([r["verdict"] for r in rs])
        out.append(base)
    return out


def rates(recs: List[dict]) -> dict:
    uns = [r for r in recs if r["label"] == "unsafe"]
    saf = [r for r in recs if r["label"] == "safe"]
    n = len(recs)
    esc = [r for r in recs if r["verdict"] == "escalate"]
    f = lambda xs, v: (sum(r["verdict"] in v for r in xs) / len(xs)) if xs else float("nan")
    return {
        "n": n, "n_unsafe": len(uns), "n_safe": len(saf),
        "detection": f(uns, ("block", "escalate")),
        "block_on_unsafe": f(uns, ("block",)),
        "false_approval": f(uns, ("approve",)),
        "false_block": f(saf, ("block",)),
        "safe_approval": f(saf, ("approve",)),
        "escalation_rate": len(esc) / n if n else float("nan"),
        "escalation_precision": (sum(r["label"] == "unsafe" for r in esc) / len(esc)) if esc else float("nan"),
        "correct": (sum((r["label"] == "unsafe" and r["verdict"] == "block") or (r["label"] == "safe" and r["verdict"] == "approve")
                        for r in recs) / n) if n else float("nan"),
        "tests": float(np.mean([r.get("n_tests_total", r["n_tests"]) for r in recs])) if recs else float("nan"),
        "ci_seconds": float(np.mean([r["ci_seconds"] for r in recs])) if recs else float("nan"),
        "ci_seconds_median": float(np.median([r["ci_seconds"] for r in recs])) if recs else float("nan"),
        "llm_seconds": float(np.mean([r["llm_seconds"] for r in recs])) if recs else float("nan"),
        "tokens": float(np.mean([r["tokens_in"] + r["tokens_out"] for r in recs])) if recs else float("nan"),
        "cost_usd": float(np.mean([r["cost_usd"] for r in recs])) if recs else float("nan"),
        "required_coverage": float(np.mean([r["required_coverage"] for r in recs])) if recs else float("nan"),
        "replans": float(np.mean([r["replans"] for r in recs])) if recs else float("nan"),
    }


def bootstrap_ci(recs: List[dict], key: str, B=2000, seed=0):
    """95% percentile bootstrap CI, resampling change cases (clustered: all intents of a case together)."""
    rng = np.random.default_rng(seed)
    by_case = defaultdict(list)
    for r in recs:
        by_case[r["case"]].append(r)
    ids = list(by_case)
    vals = []
    for _ in range(B):
        samp = [x for i in rng.integers(0, len(ids), len(ids)) for x in by_case[ids[i]]]
        v = rates(samp)[key]
        if not math.isnan(v):
            vals.append(v)
    if not vals:
        return (float("nan"), float("nan"))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def paired(a: List[dict], b: List[dict]):
    ka = {(r["case"], r["intent"]): r for r in a}
    kb = {(r["case"], r["intent"]): r for r in b}
    keys = sorted(set(ka) & set(kb))
    return [(ka[k], kb[k]) for k in keys]


def mcnemar(pairs, outcome) -> dict:
    """outcome(r)->bool. Exact McNemar on discordant pairs."""
    b = sum(outcome(x) and not outcome(y) for x, y in pairs)
    c = sum(not outcome(x) and outcome(y) for x, y in pairs)
    n = b + c
    p = 1.0 if n == 0 else float(min(1.0, 2 * stats.binom.cdf(min(b, c), n, 0.5)))
    return {"a_only": b, "b_only": c, "p": p}


def wilcoxon(pairs, key) -> dict:
    x = np.array([p[0][key] for p in pairs], float)
    y = np.array([p[1][key] for p in pairs], float)
    d = x - y
    if np.all(d == 0):
        return {"median_diff": 0.0, "p": 1.0}
    try:
        p = float(stats.wilcoxon(x, y, zero_method="wilcox").pvalue)
    except ValueError:
        p = 1.0
    return {"median_diff": float(np.median(d)), "p": p}


def holm(pvals: Dict[str, float]) -> Dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = running
    return out


def fleiss_kappa(recs: List[dict]) -> float:
    """Agreement of verdicts across repeats (categories approve/block/escalate)."""
    from statsmodels.stats.inter_rater import fleiss_kappa as fk
    g = defaultdict(list)
    for r in recs:
        g[(r["case"], r["intent"])].append(r["verdict"])
    cats = ["approve", "block", "escalate"]
    rows = [[vs.count(c) for c in cats] for vs in g.values() if len(vs) >= 2]
    if not rows:
        return float("nan")
    n = min(sum(r) for r in rows)
    rows = [r for r in rows if sum(r) == n]
    try:
        return float(fk(np.array(rows)))
    except Exception:
        return float("nan")


def unanimity(recs: List[dict]) -> float:
    g = defaultdict(list)
    for r in recs:
        g[(r["case"], r["intent"])].append(r["verdict"])
    gs = [v for v in g.values() if len(v) >= 2]
    return sum(len(set(v)) == 1 for v in gs) / len(gs) if gs else float("nan")
