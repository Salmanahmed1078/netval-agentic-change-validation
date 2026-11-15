"""Exhaustive ground-truth oracle (Phase 2, Step 2 'Ground truth').

For a change case it profiles the network before and after the change with
BOTH twin tiers exhaustively (all host pairs x all services, every route on
every device, every adjacency, every invariant, 1500-byte and jumbo DF probes
between all host pairs, failover convergence of every redundant link and the
utilisation of every link) and labels the change UNSAFE if any of these holds:
  * an invariant that held before is violated after,
  * the intended effect stated in the case's machine-readable intent spec is
    not achieved,
  * behaviour changed that the intent does not explain (reachability, routes,
    adjacencies, MTU, convergence SLA, utilisation SLA).
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Dict, List

from .model import Snapshot
from .twin import Twin, SERVICES, net
from .dsl import check_invariant, INVARIANTS


def link_subnets(s: Snapshot) -> set:
    out = set()
    for a, ia, b, ib in s.links:
        for d, i in ((a, ia), (b, ib)):
            itf = s.devices.get(d).interfaces.get(i) if d in s.devices else None
            if itf and itf.ip:
                out.add(str(net(itf.ip)))
    return out


def profile(s: Snapshot, tw: Twin = None) -> dict:
    tw = tw or Twin(s)
    eps = tw.endpoints()
    flows = {}
    for a in eps:
        for b in eps:
            if a != b:
                for sv in SERVICES:
                    flows[(a, b, sv)] = tw.allowed(a, b, sv)
    routes = {}
    for d, rib in tw.cp.rib.items():
        for p, r in rib.items():
            routes.setdefault(p, set()).add(d)
    mtu = {}
    for a in eps:
        for b in eps:
            if a != b:
                mtu[(a, b, 1500)] = tw.ping(a, b, 1500)["ok"]
                if s.hosts[a].mtu >= 9000 and s.hosts[b].mtu >= 9000:
                    mtu[(a, b, 9000)] = tw.ping(a, b, 9000)["ok"]
    conv = {}
    for a, b in s.policy.get("redundant_links", []):
        if tw.adjacency(a, b)[0] == "up":
            conv[(a, b)] = tw.convergence(a, b)["seconds"]
    loads = {}
    for x, ix, y, iy in s.links:
        loads[tuple(sorted((x, y)))] = tw.link_util(x, y)
    inv = {n: check_invariant(n, tw, "t1") for n in INVARIANTS}
    paths = {(t[0], t[1]): sorted(tuple(p) for p in tw.reach(t[0], t[1], t[3] if len(t) > 3 else "tcp/443")["paths"])
             for t in s.traffic}
    return {"flows": flows, "routes": routes, "adj": tw.all_adjacencies(), "mtu": mtu, "conv": conv,
            "loads": loads, "inv": inv, "paths": paths, "linknets": link_subnets(s), "twin": tw}


def _svcs(svc):
    return SERVICES if svc == "*" else [svc]


def oracle(pre: dict, post: dict, intent: dict, policy: dict) -> dict:
    """Return {'label': 'safe'|'unsafe', 'reasons': [...], 'intent_ok': bool, 'unintended': [...]}"""
    reasons, unintended, unmet = [], [], []
    tw = post["twin"]
    sla = policy.get("sla", {})
    like_hosts = {h for h, _ in intent.get("host_like", [])}
    # ---------------- invariants
    for n, (ok, det) in post["inv"].items():
        if not ok and pre["inv"][n][0]:
            reasons.append(f"invariant {n} violated: {det}")
    # ---------------- intended effects
    for src, dst, svc, want in intent.get("flows", []):
        bad = [s for s in _svcs(svc) if post["flows"].get((src, dst, s)) != (want == "allow")]
        if bad:
            unmet.append(f"intended {want} {src}->{dst} {svc} not achieved ({bad[:3]})")
    for prefix, want, devs in intent.get("routes", []):
        p = str(net(prefix))
        have = post["routes"].get(p, set())
        for d in devs:
            if (d in have) != (want == "present"):
                unmet.append(f"intended route {p} {want} on {d} not achieved")
    for a, b, want in intent.get("adj", []):
        st = tw.adjacency(a, b)[0]
        if st != want:
            unmet.append(f"intended adjacency {a}--{b} {want}, got {st}")
    for src, dst, via, want in intent.get("paths", []):
        if tw.path_contains(src, dst, via) != want:
            unmet.append(f"intended path {src}->{dst} via {via}={want} not achieved")
    for src, dst, size, want in intent.get("mtu", []):
        if tw.ping(src, dst, size)["ok"] != (want == "pass"):
            unmet.append(f"intended {size}-byte path {src}->{dst} {want} not achieved")
    for a, b in intent.get("drained", []):
        u = tw.link_util(a, b)
        if u > 0:
            unmet.append(f"link {a}--{b} should be drained but carries {u}%")
    for h, x in intent.get("host_like", []):
        diff = []
        for e in tw.endpoints():
            if e in (h, x):
                continue
            for sv in SERVICES:
                if post["flows"][(h, e, sv)] != post["flows"][(x, e, sv)] or \
                        post["flows"][(e, h, sv)] != post["flows"][(e, x, sv)]:
                    diff.append((e, sv))
        if diff:
            unmet.append(f"{h} does not behave like {x} after move ({diff[:3]})")
    reasons += unmet
    # ---------------- unintended behaviour changes
    explained = intent.get("flows", [])
    for k, v in post["flows"].items():
        if pre["flows"].get(k) == v:
            continue
        a, b, sv = k
        if a in like_hosts or b in like_hosts:
            continue
        if any(a == s and b == d and (svc == "*" or svc == sv) for s, d, svc, _ in explained):
            continue
        unintended.append(f"flow {a}->{b} {sv} changed {'allow' if pre['flows'].get(k) else 'deny'}->{'allow' if v else 'deny'}")
    ok_prefixes = {str(net(p)) for p, *_ in intent.get("routes", [])} | {str(net(p)) for p in intent.get("route_change_ok", [])}
    for p in set(pre["routes"]) | set(post["routes"]):
        if p in pre["linknets"] or p in post["linknets"] or p in ok_prefixes:
            continue
        if pre["routes"].get(p, set()) != post["routes"].get(p, set()):
            add = post["routes"].get(p, set()) - pre["routes"].get(p, set())
            rem = pre["routes"].get(p, set()) - post["routes"].get(p, set())
            unintended.append(f"route {p} changed (+{sorted(add)[:3]} -{sorted(rem)[:3]})")
    ok_adj = {tuple(sorted((a, b))) for a, b, _ in intent.get("adj", [])}
    for k in set(pre["adj"]) | set(post["adj"]):
        if pre["adj"].get(k) != post["adj"].get(k):
            a, b = k.split("--")
            if tuple(sorted((a, b))) not in ok_adj:
                unintended.append(f"adjacency {k} {pre['adj'].get(k)}->{post['adj'].get(k)}")
    for k, v in post["mtu"].items():
        if pre["mtu"].get(k) and not v and k[0] not in like_hosts and k[1] not in like_hosts:
            if not any(f[0] == k[0] and f[1] == k[1] for f in explained):
                unintended.append(f"{k[2]}-byte DF probe {k[0]}->{k[1]} now fails (MTU black hole)")
    cmax = sla.get("convergence_max_s", 3.0)
    planned_outage = bool(intent.get("drained")) or any(w == "down" for *_, w in intent.get("adj", []))
    for k, v in post["conv"].items():
        if planned_outage and v == float("inf"):
            continue      # loss of redundancy during planned maintenance is accepted
        if v is not None and v > cmax and (pre["conv"].get(k) is not None and pre["conv"][k] <= cmax):
            unintended.append(f"failover of {k[0]}--{k[1]} takes {v}s > SLA {cmax}s")
    umax = sla.get("link_util_max_pct", 90.0)
    for k, v in post["loads"].items():
        if v > umax and pre["loads"].get(k, 0) <= umax:
            unintended.append(f"link {k[0]}--{k[1]} at {v}% > {umax}%")
    # de-duplicate flows into a compact list
    reasons += unintended[:12]
    return {"label": "unsafe" if reasons else "safe", "reasons": reasons, "intent_ok": not unmet,
            "unintended": unintended}


def snap_hash(s: Snapshot) -> str:
    return hashlib.sha1(json.dumps(s.to_json(), sort_keys=True).encode()).hexdigest()[:12]
