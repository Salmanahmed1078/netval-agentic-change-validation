"""Deterministic baselines (Phase 2, Step 6).

B1 Fixed full suite   - the same complete regression suite on every change, golden = pre-change state
B2 Rule-based         - if/else mapping from changed config section type to a fixed test bundle
B4 Batfish-only       - static invariants + full static differential, no LLM, no emulation
(B3, the one-shot LLM, lives in agent.py)
"""
from __future__ import annotations

import time
from typing import List

from .model import Snapshot
from .twin import Twin, SERVICES
from .dsl import run_plan, INVARIANTS, COST
from .impact import structured_diff, impact
from .agent import Verdict


def _verdict_from(res, tests, ex, name) -> Verdict:
    failed = [r for r in res if r["status"] == "fail"]
    v = Verdict("block" if failed else "approve",
                ("; ".join(f"{r['test']} -> {r['detail'][:70]}" for r in failed[:3]) if failed else f"{name}: all {len(tests)} tests passed"))
    v.tests, v.results, v.tool_seconds = tests, res, ex["tool_seconds"]
    return v


def _batched_cost(res) -> float:
    """Realistic CI time: Batfish answers all-pairs reachability per (src, service) and routes per device in one
    query; one traffic run measures all links; probes and failover experiments cost per test."""
    t1_batches, t2_cost, t2_load, used = set(), 0.0, False, set()
    for r in res:
        t = r["test"]
        tier = r.get("tier")
        if tier:
            used.add(tier)
        if tier == "t1":
            if t["t"] == "reach":
                t1_batches.add(("reach", t["src"], t["svc"]))
            elif t["t"] == "route":
                t1_batches.add(("route", t["dev"]))
            elif t["t"] == "adj":
                t1_batches.add(("adj",))
            else:
                t1_batches.add((t["t"], str(t)))
        elif tier == "t2":
            if t["t"] == "load":
                t2_load = True
            elif t["t"] == "conv":
                t2_cost += COST["t2_conv"]
            else:
                t2_cost += COST["t2_probe"]
    total = len(t1_batches) * COST["t1_query"] + t2_cost + (COST["t2_load"] if t2_load else 0)
    total += (COST["t1_setup"] if "t1" in used else 0) + (COST["t2_setup"] if "t2" in used else 0)
    return total


# =========================================================================== B1
class FullSuite:
    name = "B1 full suite"

    def validate(self, pre: Snapshot, post: Snapshot, ticket: str = "", seed: int = 1, tpre=None, tpost=None) -> Verdict:
        t0 = time.time()
        tpre, tpost = tpre or Twin(pre), tpost or Twin(post)
        eps = tpre.endpoints()
        tests = [{"t": "reach", "src": a, "dst": b, "svc": s, "expect": "unchanged"}
                 for a in eps for b in eps if a != b for s in SERVICES]
        prefixes = sorted({p for rib in tpre.cp.rib.values() for p in rib} | {p for rib in tpost.cp.rib.values() for p in rib})
        routers = sorted(d for d, x in pre.devices.items() if x.kind == "router")
        tests += [{"t": "route", "dev": d, "prefix": p, "expect": "unchanged"} for d in routers for p in prefixes]
        tests += [{"t": "adj", "a": a, "b": b, "expect": "unchanged"} for a, _, b, _ in pre.links
                  if pre.devices[a].ospf or pre.devices[b].ospf]
        tests += [{"t": "inv", "name": n} for n in INVARIANTS]
        tests += [{"t": "mtu", "src": a, "dst": b, "size": 1500, "expect": "pass"} for a in eps for b in eps
                  if a != b and tpre.ping(a, b, 1500)["ok"]]
        sla = pre.policy["sla"]
        tests += [{"t": "conv", "a": a, "b": b, "max_s": sla["convergence_max_s"]} for a, b in pre.policy["redundant_links"]]
        tests += [{"t": "load", "a": a, "b": b, "max_pct": sla["link_util_max_pct"]} for a, _, b, _ in pre.links]
        ex = run_plan(tests, tpre, tpost)
        ex["tool_seconds"] = _batched_cost(ex["results"])
        v = _verdict_from(ex["results"], tests, ex, self.name)
        v.compute_seconds = time.time() - t0
        return v


# =========================================================================== B2
class RuleBased:
    name = "B2 rule-based"

    def validate(self, pre: Snapshot, post: Snapshot, ticket: str = "", seed: int = 1, tpre=None, tpost=None) -> Verdict:
        t0 = time.time()
        tpre, tpost = tpre or Twin(pre), tpost or Twin(post)
        sd = structured_diff(pre, post)
        eps = tpre.endpoints()
        behind = lambda d: [h for h in eps if (tpre.dp.gateway(h)[0] or ("",))[0] == d or pre.hosts[h].attach[0] == d]
        tests: List[dict] = [{"t": "inv", "name": n} for n in INVARIANTS]

        def reach_from(hosts):
            for a in hosts:
                for b in eps:
                    if a != b:
                        tests.append({"t": "reach", "src": a, "dst": b, "svc": "*", "expect": "unchanged"})
                        tests.append({"t": "reach", "src": b, "dst": a, "svc": "*", "expect": "unchanged"})

        def links_of(d):
            return [(a, b) for a, _, b, _ in pre.links if d in (a, b) and pre.devices[a].ospf and pre.devices[b].ospf]
        for x in sd:
            d, sec = x["device"], x["section"]
            lines = x.get("added", []) + x.get("removed", [])
            if d.startswith("host:"):                                   # VLAN move / re-address
                reach_from([d[5:]])
                continue
            if sec.startswith("ip access-list") or any("access-group" in l for l in lines):
                reach_from(behind(d))
            if sec.startswith("ip route"):
                for l in lines:
                    p = l.split()[2]
                    tests += [{"t": "route", "dev": r, "prefix": p, "expect": "unchanged"} for r in pre.devices
                              if pre.devices[r].kind == "router"]
                reach_from(behind(d))
            if sec.startswith("router ospf") or any(k in l for l in lines for k in ("cost", "hello", "dead", "bfd", "shutdown")):
                tests += [{"t": "adj", "a": a, "b": b, "expect": "unchanged"} for a, b in links_of(d)]
                reach_from(behind(d))
                if any(k in l for l in lines for k in ("hello", "dead", "bfd", "throttle")):
                    tests += [{"t": "conv", "a": a, "b": b, "max_s": pre.policy["sla"]["convergence_max_s"]} for a, b in links_of(d)]
            if sec.startswith("router bgp") or sec.startswith("ip prefix-list") or sec.startswith("route-map"):
                nbrs = [b for a, _, b, _ in pre.links if a == d] + [a for a, _, b, _ in pre.links if b == d]
                prefixes = sorted({p for n in nbrs for p in tpre.route_table(n)} | {p for n in nbrs for p in tpost.route_table(n)})
                tests += [{"t": "route", "dev": n, "prefix": p, "expect": "unchanged"} for n in nbrs for p in prefixes]
            if any(l.startswith("mtu") for l in lines) and sec.startswith("interface"):
                ifn = sec.split()[1]
                for a, ia, b, ib in pre.links:
                    if (a, ia) == (d, ifn) or (b, ib) == (d, ifn):
                        tests.append({"t": "adj", "a": a, "b": b, "expect": "unchanged"})
                        for s_ in eps:
                            for t_ in eps:
                                r = tpre.reach(s_, t_, "icmp")
                                if s_ != t_ and r["disp"] == "ACCEPTED" and any(a in p and b in p for p in r["paths"]):
                                    tests.append({"t": "mtu", "src": s_, "dst": t_, "size": 1500, "expect": "pass"})
                                    break
            if sec.startswith("ip nat") or "NAT" in sec or any("nat" in l for l in lines):
                ext = [h for h in eps if pre.hosts[h].attach[0] in pre.policy["invariants"]["no_leak"]["externals"]]
                for a in eps:
                    for b in ext:
                        if a != b:
                            tests.append({"t": "reach", "src": a, "dst": b, "svc": "*", "expect": "unchanged"})
            if "redistribute" in " ".join(lines):
                prefixes = sorted({p for rib in tpost.cp.rib.values() for p in rib} | {p for rib in tpre.cp.rib.values() for p in rib})
                tests += [{"t": "route", "dev": r, "prefix": p, "expect": "unchanged"} for r in pre.devices
                          if pre.devices[r].kind == "router" for p in prefixes]
        seen, uniq = set(), []
        for t in tests:
            k = str(sorted(t.items()))
            if k not in seen:
                seen.add(k)
                uniq.append(t)
        ex = run_plan(uniq, tpre, tpost)
        ex["tool_seconds"] = _batched_cost(ex["results"])
        v = _verdict_from(ex["results"], uniq, ex, self.name)
        v.compute_seconds = time.time() - t0
        return v


# =========================================================================== B4
class BatfishOnly:
    name = "B4 Batfish-only"

    def validate(self, pre: Snapshot, post: Snapshot, ticket: str = "", seed: int = 1, tpre=None, tpost=None) -> Verdict:
        t0 = time.time()
        tpre, tpost = tpre or Twin(pre), tpost or Twin(post)
        tests = [{"t": "inv", "name": n} for n in INVARIANTS]
        ex = run_plan(tests, tpre, tpost, tiers=("t1",))
        ents = impact(pre, post, tpre, tpost)
        diffs = [e for e in ents if e["kind"] in ("flow", "route", "adj")]
        failed = [r for r in ex["results"] if r["status"] == "fail"]
        # differential queries: 1 per service (reachability) + 1 routes + 1 adjacency
        tool = COST["t1_setup"] + len(tests) * 3 * COST["t1_query"] + (len(SERVICES) + 2) * COST["t1_query"]
        if failed:
            v = Verdict("block", "; ".join(f"{r['test']['name']}: {r['detail'][:80]}" for r in failed))
        elif diffs:
            v = Verdict("escalate", f"{len(diffs)} behavioural differences found; cannot tell if intended")
        else:
            v = Verdict("approve", "invariants hold and no behavioural difference")
        v.tests = tests + [{"t": "differential", "kind": e["kind"]} for e in diffs]
        v.results, v.tool_seconds = ex["results"], tool
        v.compute_seconds = time.time() - t0
        return v
