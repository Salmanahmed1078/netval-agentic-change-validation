"""Preprocessing (Phase 2, Step 3) and impact analysis (Step 5, agent 2).

* structured_diff   - per device / per config section before-after (from the PR text)
* unified_diff      - plain text Git-style diff
* semantic_diff     - Tier-1 differential analysis: which flows, routes, adjacencies and
                      traffic paths behave differently (like Batfish differentialReachability)
* impact            - blast radius: semantic diff + T2-relevant config objects (MTU / timers)
                      + links that receive shifted traffic, each with an id and a coverage key
* subgraph          - k-hop neighbourhood of the changed devices
"""
from __future__ import annotations

import difflib
import json
from collections import defaultdict
from typing import Dict, List

from .model import Snapshot
from .twin import Twin, SERVICES, net


# =========================================================================== text diffs
def unified_diff(pre: Snapshot, post: Snapshot) -> str:
    a, b = pre.files(), post.files()
    out = []
    for f in sorted(set(a) | set(b)):
        if a.get(f) != b.get(f):
            out += difflib.unified_diff((a.get(f) or "").splitlines(True), (b.get(f) or "").splitlines(True),
                                        f"a/{f}", f"b/{f}", n=2)
    return "".join(out)


def _sections(text: str) -> Dict[str, List[str]]:
    secs, cur = {}, None
    for line in text.splitlines():
        if line in ("!", "end") or not line.strip():
            cur = None
            continue
        if not line.startswith(" "):
            if line.startswith("ip route") or line.startswith("ip prefix-list") or line.startswith("ip nat"):
                key = " ".join(line.split()[:3])
                secs.setdefault(key, []).append(line)
                cur = None
                continue
            cur = line
            secs.setdefault(cur, [])
        elif cur:
            secs[cur].append(line.strip())
    return secs


def structured_diff(pre: Snapshot, post: Snapshot) -> List[dict]:
    out = []
    for d in sorted(set(pre.configs) | set(post.configs)):
        a, b = _sections(pre.configs.get(d, "")), _sections(post.configs.get(d, ""))
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                rem = [l for l in a.get(k, []) if l not in b.get(k, [])]
                add = [l for l in b.get(k, []) if l not in a.get(k, [])]
                out.append({"device": d, "section": k, "removed": rem, "added": add})
    ph, qh = {h["name"]: h for h in pre.hosts_raw}, {h["name"]: h for h in post.hosts_raw}
    for h in sorted(set(ph) | set(qh)):
        if ph.get(h) != qh.get(h):
            ch = {k: [ph.get(h, {}).get(k), qh.get(h, {}).get(k)] for k in ("ip", "gw", "attach", "group")
                  if ph.get(h, {}).get(k) != qh.get(h, {}).get(k)}
            out.append({"device": f"host:{h}", "section": "inventory", "changed": ch})
    return out


def changed_devices(sd: List[dict]) -> List[str]:
    return sorted({x["device"] for x in sd if not x["device"].startswith("host:")})


# =========================================================================== subgraph
def subgraph(s: Snapshot, seeds: List[str], k: int = 2) -> dict:
    adj = defaultdict(set)
    for a, ia, b, ib in s.links:
        adj[a].add(b)
        adj[b].add(a)
    for h in s.hosts.values():
        adj[h.attach[0]].add(h.name)
        adj[h.name].add(h.attach[0])
    seen = {x: 0 for x in seeds}
    q = list(seeds)
    while q:
        n = q.pop(0)
        if seen[n] >= k:
            continue
        for m in adj[n]:
            if m not in seen:
                seen[m] = seen[n] + 1
                q.append(m)
    nodes = sorted(n for n in seen if n in s.devices)
    hosts = sorted(n for n in seen if n in s.hosts)
    links = []
    for a, ia, b, ib in s.links:
        if a in seen and b in seen:
            A = s.devices[a].interfaces.get(ia)
            links.append(f"{a}--{b} ({A.bandwidth if A else '?'} Mbps)")
    return {"devices": nodes, "hosts": hosts, "links": links}


# =========================================================================== semantic diff + impact
def _link_of(s: Snapshot, dev: str, ifn: str):
    for a, ia, b, ib in s.links:
        if (a, ia) == (dev, ifn) or (b, ib) == (dev, ifn):
            return tuple(sorted((a, b)))
    return None


def impact(pre: Snapshot, post: Snapshot, tpre: Twin, tpost: Twin, sd: List[dict] = None) -> List[dict]:
    sd = sd if sd is not None else structured_diff(pre, post)
    ents: List[dict] = []
    eps = tpost.endpoints()
    # --- flows
    for a in eps:
        for b in eps:
            if a == b:
                continue
            ch = [(sv, tpre.allowed(a, b, sv), tpost.allowed(a, b, sv)) for sv in SERVICES]
            ch = [c for c in ch if c[1] != c[2]]
            if not ch:
                continue
            dirs = {c[2] for c in ch}
            if len(ch) == len(SERVICES) and len(dirs) == 1:
                ents.append({"kind": "flow", "src": a, "dst": b, "svc": "*",
                             "before": "allow" if ch[0][1] else "deny", "after": "allow" if ch[0][2] else "deny",
                             "why": tpost.reach(a, b, "icmp")["detail"][:80]})
            else:
                for sv, x, y in ch:
                    ents.append({"kind": "flow", "src": a, "dst": b, "svc": sv,
                                 "before": "allow" if x else "deny", "after": "allow" if y else "deny",
                                 "why": tpost.reach(a, b, sv)["detail"][:80]})
    # --- routes
    from .oracle import link_subnets
    ln = link_subnets(pre) | link_subnets(post)
    rp, rq = defaultdict(set), defaultdict(set)
    for d, rib in tpre.cp.rib.items():
        for p in rib:
            rp[p].add(d)
    for d, rib in tpost.cp.rib.items():
        for p in rib:
            rq[p].add(d)
    for p in sorted(set(rp) | set(rq)):
        if p in ln or rp[p] == rq[p]:
            continue
        ents.append({"kind": "route", "prefix": p, "added_on": sorted(rq[p] - rp[p])[:6],
                     "removed_on": sorted(rp[p] - rq[p])[:6]})
    # --- adjacencies / sessions
    aa, ab = tpre.all_adjacencies(), tpost.all_adjacencies()
    for k in sorted(set(aa) | set(ab)):
        if aa.get(k) != ab.get(k):
            a, b = k.split("--")
            ents.append({"kind": "adj", "a": a, "b": b, "before": aa.get(k), "after": ab.get(k),
                         "why": tpost.adjacency(a, b)[1]})
    sa, sb = set(tpre.bgp_sessions()), set(tpost.bgp_sessions())
    for x in sorted(sa ^ sb):
        ents.append({"kind": "adj", "a": x[0], "b": x[1], "before": "up" if x in sa else "down",
                     "after": "up" if x in sb else "down", "why": "bgp session"})
    # --- traffic paths + links receiving shifted demand (static projection of the telemetry matrix over
    #     Tier-1 forwarding paths incl. ECMP weights; no capacities / utilisation - that is Tier 2's job)
    def demand(tw):
        acc = defaultdict(float)
        who = defaultdict(set)
        for t in post.traffic:
            src, dst, mb = t[0], t[1], t[2]
            svc = t[3] if len(t) > 3 else "tcp/443"
            a = tw.dp.resolve(dst)
            if a is None:
                continue
            for p in tw.dp.trace(src, a, svc):
                if p.disposition != "ACCEPTED":
                    continue
                for d, o in p.links:
                    lk = _link_of(post, d, o)
                    if lk:
                        acc[lk] += mb * p.weight
                        who[lk].add(f"{src}->{dst}")
        return acc, who
    d0, _ = demand(tpre)
    d1, w1 = demand(tpost)
    for t in post.traffic:
        src, dst, svc = t[0], t[1], (t[3] if len(t) > 3 else "tcp/443")
        p0 = tpre.reach(src, dst, svc)
        p1 = tpost.reach(src, dst, svc)
        if sorted(map(tuple, p0["paths"])) != sorted(map(tuple, p1["paths"])) and p1["disp"] == "ACCEPTED":
            ents.append({"kind": "path", "src": src, "dst": dst, "before": p0["paths"][:3], "after": p1["paths"][:3]})
    for lk in sorted(set(d0) | set(d1)):
        if d1.get(lk, 0) > d0.get(lk, 0) * 1.05 + 1e-6:
            ents.append({"kind": "load", "a": lk[0], "b": lk[1], "new_demands": sorted(w1[lk])[:6],
                         "detail": f"carries {d1[lk] / max(d0.get(lk, 0), 1e-9):.1f}x the previous demand" if d0.get(lk) else "newly used"})
    # --- T2-relevant config objects (from the structured diff)
    seen = set()
    for x in sd:
        d = x["device"]
        if d.startswith("host:"):
            continue
        sec = x["section"]
        lines = x.get("added", []) + x.get("removed", [])
        if sec.startswith("interface "):
            ifn = sec.split()[1]
            lk = _link_of(post, d, ifn)
            if lk and any(l.startswith("mtu") for l in lines) and ("mtu",) + lk not in seen:
                seen.add(("mtu",) + lk)
                ents.append({"kind": "mtu_change", "a": lk[0], "b": lk[1],
                             "detail": f"{d}:{ifn} " + ", ".join(l for l in lines if l.startswith("mtu"))})
            if lk and any(("hello" in l or "dead" in l or l == "bfd") for l in lines) and ("tim",) + lk not in seen:
                seen.add(("tim",) + lk)
                ents.append({"kind": "timer_change", "a": lk[0], "b": lk[1],
                             "detail": f"{d}:{ifn} " + ", ".join(l for l in lines if "hello" in l or "dead" in l or l == "bfd")})
        if sec == "router ospf" and any("throttle" in l for l in lines):
            for a, ia, b, ib in post.links:
                if d in (a, b) and ("tim", a, b) not in seen and tpost.adjacency(a, b)[0] == "up":
                    seen.add(("tim", a, b))
                    ents.append({"kind": "timer_change", "a": min(a, b), "b": max(a, b),
                                 "detail": f"{d} spf throttle changed"})
                    break
    if not ents:
        ents.append({"kind": "no_behaviour_change", "detail": "configuration changed but Tier-1 analysis shows no behavioural difference"})
    for i, e in enumerate(ents, 1):
        e["id"] = f"I{i}"
    return ents


def impact_key(e: dict):
    k = e["kind"]
    if k == "flow":
        return ("flow", e["src"], e["dst"], e["svc"])
    if k == "route":
        return ("route", e["prefix"])
    if k == "adj":
        return ("adj",) + tuple(sorted((e["a"], e["b"])))
    if k == "path":
        return ("path", e["src"], e["dst"])
    if k == "load":
        return ("load", e["a"], e["b"])
    if k == "mtu_change":
        return ("mtu-link", e["a"], e["b"])
    if k == "timer_change":
        return ("conv", e["a"], e["b"])
    return None


def covered(e: dict, tests: List[dict], tpost: Twin) -> bool:
    """Does at least one (valid) test in the plan touch this impact entry?"""
    k = impact_key(e)
    if k is None:
        return True
    for t in tests:
        typ = t.get("t")
        if k[0] == "flow" and typ == "like" and t.get("host") in (k[1], k[2]):
            return True
        if k[0] == "flow" and typ in ("reach", "acl") and t.get("src") == k[1] and t.get("dst") == k[2]:
            if k[3] == "*" or t.get("svc") in (k[3], "*"):
                return True
        elif k[0] == "route" and typ == "route":
            try:
                if str(net(t.get("prefix"))) == k[1]:
                    return True
            except Exception:
                pass
        elif k[0] == "adj" and typ == "adj" and sorted([t.get("a"), t.get("b")]) == list(k[1:]):
            return True
        elif k[0] == "path" and ((typ == "path" and t.get("src") == k[1] and t.get("dst") == k[2]) or
                                 (typ == "reach" and t.get("src") == k[1] and t.get("dst") == k[2])):
            return True
        elif k[0] == "load" and typ == "load" and sorted([t.get("a"), t.get("b")]) == list(k[1:]):
            return True
        elif k[0] == "conv" and typ == "conv" and sorted([t.get("a"), t.get("b")]) == list(k[1:]):
            return True
        elif k[0] == "mtu-link" and typ == "mtu":
            try:
                paths = tpost.reach(t["src"], t["dst"], "icmp")["paths"]
                if any(k[1] in p and k[2] in p for p in paths):
                    return True
            except Exception:
                pass
    return False


def coverage(ents: List[dict], tests: List[dict], tpost: Twin) -> float:
    rel = [e for e in ents if impact_key(e) is not None]
    if not rel:
        return 1.0
    return sum(covered(e, tests, tpost) for e in rel) / len(rel)
