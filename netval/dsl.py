"""Typed test language (Phase 2, Step 4) and its executor on the two-tier twin.

A test is a small JSON object.  The LLM only fills these; it never writes code.

  {"t":"reach", "src":H, "dst":H|router, "svc":"tcp/443"|"*", "expect":"allow|deny|unchanged"}
  {"t":"path",  "src":H, "dst":H, "via":router, "expect":true|false}
  {"t":"route", "dev":router, "prefix":P, "expect":"present|absent|unchanged"}
  {"t":"acl",   "dev":router, "acl":NAME, "src":H, "dst":H, "svc":S, "expect":"permit|deny"}
  {"t":"adj",   "a":router, "b":router, "expect":"up|down|unchanged"}
  {"t":"mtu",   "src":H, "dst":H, "size":int, "expect":"pass|fail"}         (Tier 2 only)
  {"t":"conv",  "a":router, "b":router, "max_s":float}                     (Tier 2 only)
  {"t":"load",  "a":router, "b":router, "max_pct":float}                   (Tier 2 only)
  {"t":"like",  "host":H, "as":H}       (after a move, H has exactly the same access as host X, all others/services)
  {"t":"inv",   "name":"no_loops|mgmt_reachable|isolation|no_leak"}
"""
from __future__ import annotations

import ipaddress
from typing import Dict, List, Optional, Tuple

from .twin import Twin, SERVICES, net

T1_TYPES = {"reach", "path", "route", "acl", "adj", "inv", "like"}
T2_TYPES = {"reach", "path", "route", "adj", "mtu", "conv", "load", "inv", "like"}
T2_ONLY = {"mtu", "conv", "load"}
T2_SAMPLE_SVCS = ["icmp", "tcp/443"]
INVARIANTS = ["no_loops", "mgmt_reachable", "isolation", "no_leak"]

# Modelled CI wall-clock cost (seconds) of running a test on real tooling.
# Assumptions documented in the report (Section 4.6): Batfish snapshot init ~25 s
# for pre+post, ~1 s per query; containerlab+FRR boot and initial convergence
# ~150 s, ~3 s per probe, ~45 s per failover experiment, ~20 s per load run.
COST = {"t1_setup": 25.0, "t1_query": 1.0, "t2_setup": 150.0, "t2_probe": 3.0, "t2_conv": 45.0, "t2_load": 20.0}

REQUIRED_KEYS = {
    "reach": ["src", "dst", "svc", "expect"], "path": ["src", "dst", "via", "expect"],
    "route": ["dev", "prefix", "expect"], "acl": ["dev", "acl", "src", "dst", "svc", "expect"],
    "adj": ["a", "b", "expect"], "mtu": ["src", "dst", "size", "expect"], "conv": ["a", "b", "max_s"],
    "load": ["a", "b", "max_pct"], "inv": ["name"], "like": ["host", "as"],
}


# =========================================================================== validation
def validate(t: dict, twin: Twin) -> Optional[str]:
    if not isinstance(t, dict) or t.get("t") not in REQUIRED_KEYS:
        return f"unknown test type {t.get('t') if isinstance(t, dict) else t!r}"
    for k in REQUIRED_KEYS[t["t"]]:
        if k not in t:
            return f"missing field '{k}'"
    names = set(twin.s.hosts) | set(twin.s.devices)
    for k in ("host", "as"):
        if k in t and t[k] not in twin.s.hosts:
            return f"unknown host '{t[k]}'"
    for k in ("src", "dst"):
        if k in t and t[k] not in names:
            return f"unknown endpoint '{t[k]}'"
    for k in ("dev", "a", "b", "via"):
        if k in t and t[k] not in twin.s.devices:
            return f"unknown device '{t[k]}'"
    if "svc" in t and t["svc"] != "*" and t["svc"] not in SERVICES:
        return f"unknown service '{t['svc']}'"
    if t["t"] == "route":
        try:
            ipaddress.ip_network(t["prefix"], strict=False)
        except Exception:
            return f"bad prefix '{t['prefix']}'"
    if t["t"] == "inv" and t["name"] not in INVARIANTS:
        return f"unknown invariant '{t['name']}'"
    if t["t"] in ("conv", "load", "adj") and not any({x, y} == {t["a"], t["b"]} for x, _, y, _ in twin.s.links):
        return f"no link between {t['a']} and {t['b']}"
    if t["t"] == "acl" and t["acl"] not in twin.s.devices[t["dev"]].acls:
        return f"no ACL {t['acl']} on {t['dev']}"
    exp = {"reach": ("allow", "deny", "unchanged"), "route": ("present", "absent", "unchanged"),
           "adj": ("up", "down", "unchanged"), "acl": ("permit", "deny"), "mtu": ("pass", "fail")}
    if t["t"] in exp and t["expect"] not in exp[t["t"]]:
        return f"bad expectation '{t['expect']}'"
    try:
        if "size" in t:
            int(t["size"])
        if "max_s" in t:
            float(t["max_s"])
        if "max_pct" in t:
            float(t["max_pct"])
    except Exception:
        return "non-numeric threshold"
    return None


def tier_for(t: dict, tiers: Tuple[str, ...]) -> Optional[str]:
    typ = t.get("t")
    if typ in T2_ONLY:
        return "t2" if "t2" in tiers else None
    if "t1" in tiers and typ in T1_TYPES:
        return "t1"
    if "t2" in tiers and typ in T2_TYPES:
        return "t2"
    return None


# =========================================================================== invariants
def check_invariant(name: str, tw: Twin, tier: str = "t1") -> Tuple[bool, str]:
    pol = tw.s.policy["invariants"]
    svcs = SERVICES if tier == "t1" else T2_SAMPLE_SVCS
    if name == "no_loops":
        eps = tw.endpoints()
        for a in eps:
            for b in eps:
                if a != b:
                    d = tw.reach(a, b, "icmp")["disp"]
                    if "LOOP" in d:
                        return False, f"forwarding loop {a}->{b}"
        return True, "no loops"
    if name == "mgmt_reachable":
        m = pol["mgmt_reachable"]
        bad = [t for t in m["targets"] if tw.reach(m["src"], t, "icmp")["disp"] != "ACCEPTED"]
        return (not bad), ("all managed devices reachable" if not bad else f"unreachable from {m['src']}: {bad[:4]}")
    if name == "isolation":
        bad = [(a, b, s) for a, b in pol["isolation"] for s in svcs if tw.allowed(a, b, s)]
        return (not bad), ("isolation holds" if not bad else f"isolation broken: {bad[:3]}")
    if name == "no_leak":
        nl = pol["no_leak"]
        bad = []
        for e in nl["externals"]:
            for p, r in tw.route_table(e).items():
                if r.proto == "connected":
                    continue
                for f in nl["forbidden"]:
                    if net(p).subnet_of(net(f)):
                        bad.append((e, p))
        return (not bad), ("no internal prefixes leaked" if not bad else f"leaked: {bad[:3]}")
    return False, "unknown invariant"


# =========================================================================== execution
def _reach_obs(tw: Twin, t: dict, tier: str) -> Dict[str, bool]:
    if t["svc"] == "*":
        svcs = SERVICES if tier == "t1" else T2_SAMPLE_SVCS
    else:
        svcs = [t["svc"]]
    out = {}
    for s in svcs:
        if tier == "t1":
            out[s] = tw.allowed(t["src"], t["dst"], s)
        else:
            out[s] = tw.ping(t["src"], t["dst"], 64, s)["ok"]
    return out


def execute(t: dict, pre: Twin, post: Twin, tiers=("t1", "t2")) -> dict:
    """Run one typed test. Returns status pass|fail|error|skipped plus evidence."""
    err = validate(t, post)
    if err:
        return {"status": "error", "tier": None, "detail": err, "cost": 0.0}
    tier = tier_for(t, tiers)
    if tier is None:
        return {"status": "skipped", "tier": None, "detail": "no enabled twin tier supports this test", "cost": 0.0}
    typ = t["t"]
    cost = COST["t1_query"] if tier == "t1" else COST["t2_probe"]
    ok, obs = False, ""
    if typ == "reach":
        a = _reach_obs(post, t, tier)
        if t["expect"] == "unchanged":
            b = _reach_obs(pre, t, tier)
            changed = {s: (b[s], a[s]) for s in a if a[s] != b[s]}
            ok = not changed
            obs = "unchanged" if ok else f"changed {changed}"
        else:
            want = t["expect"] == "allow"
            wrong = [s for s, v in a.items() if v != want]
            ok = not wrong
            r = post.reach(t["src"], t["dst"], wrong[0] if wrong else list(a)[0]) if tier == "t1" else None
            obs = f"{'all ' + t['expect'] if ok else 'violations ' + str(wrong)}" + (f" ({r['disp']} {r['detail']})" if r and not ok else "")
    elif typ == "path":
        got = post.path_contains(t["src"], t["dst"], t["via"])
        want = t["expect"] in (True, "true", "True")
        ok = got == want
        obs = f"paths {post.reach(t['src'], t['dst'], 'icmp')['paths'][:2]}"
    elif typ == "route":
        got = post.route_present(t["dev"], t["prefix"])
        if t["expect"] == "unchanged":
            ok = got == pre.route_present(t["dev"], t["prefix"])
        else:
            ok = got == (t["expect"] == "present")
        obs = "present" if got else "absent"
    elif typ == "acl":
        got = post.acl_decision(t["dev"], t["acl"], t["src"], t["dst"], t["svc"])
        ok = got == t["expect"]
        obs = got
    elif typ == "adj":
        st, why = post.adjacency(t["a"], t["b"])
        if t["expect"] == "unchanged":
            ok = st == pre.adjacency(t["a"], t["b"])[0]
        else:
            ok = st == t["expect"]
        obs = f"{st} {why}".strip()
    elif typ == "mtu":
        r = post.ping(t["src"], t["dst"], int(t["size"]))
        ok = r["ok"] == (t["expect"] == "pass")
        obs = f"{'delivered' if r['ok'] else r['disp'] + ' ' + r['detail']}"
    elif typ == "conv":
        cost = COST["t2_conv"]
        r = post.convergence(t["a"], t["b"])
        s = r["seconds"]
        ok = s is not None and s <= float(t["max_s"])
        obs = f"{s}s ({r['detail']})"
    elif typ == "load":
        cost = COST["t2_load"]
        u = post.link_util(t["a"], t["b"])
        ok = u <= float(t["max_pct"])
        obs = f"{u}% utilisation"
    elif typ == "like":
        svcs = SERVICES if tier == "t1" else T2_SAMPLE_SVCS
        h, x = t["host"], t["as"]
        f = (lambda a, b, sv: post.allowed(a, b, sv)) if tier == "t1" else (lambda a, b, sv: post.ping(a, b, 64, sv)["ok"])
        diff = [(e, sv) for e in post.endpoints() if e not in (h, x) for sv in svcs
                if f(h, e, sv) != f(x, e, sv) or f(e, h, sv) != f(e, x, sv)]
        ok = not diff
        obs = f"{h} has the same access as {x}" if ok else f"differs from {x}: {diff[:4]}"
        cost = COST["t1_query"] * 2 if tier == "t1" else COST["t2_probe"] * 8
    elif typ == "inv":
        cost = COST["t1_query"] * 3 if tier == "t1" else COST["t2_probe"] * 6
        ok, obs = check_invariant(t["name"], post, tier)
    return {"status": "pass" if ok else "fail", "tier": tier, "detail": obs, "cost": cost}


def run_plan(tests: List[dict], pre: Twin, post: Twin, tiers=("t1", "t2")) -> dict:
    res = [dict(test=t, **execute(t, pre, post, tiers)) for t in tests]
    used = {r["tier"] for r in res if r["tier"]}
    setup = (COST["t1_setup"] if "t1" in used else 0) + (COST["t2_setup"] if "t2" in used else 0)
    return {"results": res, "tool_seconds": setup + sum(r["cost"] for r in res), "tiers_used": sorted(used)}


# =========================================================================== coverage keys
def test_keys(t: dict) -> List[tuple]:
    """Coverage keys a test provides (used for required-test coverage and impact coverage)."""
    typ = t.get("t")
    if typ == "reach":
        return [("flow", t.get("src"), t.get("dst"), t.get("svc"))]
    if typ == "path":
        return [("path", t.get("src"), t.get("dst"))]
    if typ == "route":
        try:
            return [("route", str(ipaddress.ip_network(t.get("prefix"), strict=False)))]
        except Exception:
            return []
    if typ == "acl":
        return [("flow", t.get("src"), t.get("dst"), t.get("svc")), ("acl", t.get("dev"), t.get("acl"))]
    if typ in ("adj", "conv", "load"):
        a, b = sorted([str(t.get("a")), str(t.get("b"))])
        return [({"adj": "adj", "conv": "conv", "load": "load"}[typ], a, b)]
    if typ == "mtu":
        return [("mtu", t.get("src"), t.get("dst"))]
    if typ == "inv":
        return [("inv", t.get("name"))]
    if typ == "like":
        return [("flow", t.get("host"), t.get("as"), "*"), ("like", t.get("host"))]
    return []


def key_covers(have: tuple, want: tuple) -> bool:
    if have[0] != want[0]:
        return False
    if have[0] == "flow":
        return have[1] == want[1] and have[2] == want[2] and (have[3] == want[3] or have[3] == "*")
    return have == want
