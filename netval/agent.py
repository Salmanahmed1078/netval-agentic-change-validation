"""The proposed agentic validator (Phase 2, Step 5) and the one-shot LLM baseline (B3).

Pipeline (bounded loop, max 3 replans):
  1. Intent analyzer   (LLM)            ticket + diff        -> goal, scope, expected effects, confidence
  2. Impact analyzer   (deterministic)  Tier-1 semantic diff -> blast radius with ids (impact.py)
  3. Test planner      (LLM)            labels every impact entry intended / unintended / unsure and adds
                                         change-specific tests in the typed DSL; entries are compiled to DSL tests
  4. Test executor     (deterministic)  runs the plan on the two-tier twin (dsl.py)
  5. Evidence judge    (deterministic)  block / approve / escalate; missing evidence -> replan
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import Snapshot
from .twin import Twin, SERVICES, net
from .dsl import run_plan, validate, INVARIANTS, T2_ONLY
from .impact import structured_diff, impact, impact_key, covered, subgraph, changed_devices, unified_diff
from .llm import LLM

SLA_CONV, SLA_UTIL = 3.0, 90.0

DSL_DOC = """Typed test language (JSON objects; use ONLY these types and only names that exist):
  {"t":"reach","src":HOST,"dst":HOST|ROUTER,"svc":SVC|"*","expect":"allow"|"deny"|"unchanged"}
  {"t":"path","src":HOST,"dst":HOST,"via":ROUTER,"expect":true|false}
  {"t":"route","dev":ROUTER,"prefix":"a.b.c.d/n","expect":"present"|"absent"|"unchanged"}
  {"t":"adj","a":ROUTER,"b":ROUTER,"expect":"up"|"down"|"unchanged"}          (OSPF adjacency on a link)
  {"t":"mtu","src":HOST,"dst":HOST,"size":1500|9000,"expect":"pass"|"fail"}  (DF probe, emulation tier)
  {"t":"conv","a":ROUTER,"b":ROUTER,"max_s":3}                               (failover time of a link, emulation)
  {"t":"load","a":ROUTER,"b":ROUTER,"max_pct":90}                            (link utilisation, emulation)
  {"t":"like","host":HOST,"as":HOST}     (after a VLAN move / re-addressing: host has exactly the same access as the
                                          reference host, towards and from every other host and service)
SVC is one of: icmp, tcp/22, tcp/25, tcp/80, tcp/443, tcp/5432, udp/53."""

INTENT_SYS = """You are the intent analyzer of a network change-validation pipeline.
Read the change ticket and state what the TICKET asks for. The config diff is shown only so you can name devices;
it is NOT evidence of intent (the diff may contain mistakes - that is what we are checking).
Reply with JSON only:
{"ticket_says": "<paraphrase of what the ticket text itself requests, or 'nothing' if it only gives an ID>",
 "goal": "<one sentence>", "change_type": "acl|static_route|ospf|bgp_policy|vlan_move|mtu|maintenance|nat_redistribution|other",
 "scope": ["<devices/hosts named in the ticket>"],
 "expected_effects": ["<short, concrete, testable statements THE TICKET promises>"],
 "must_not_change": ["<things the ticket says must stay the same>"],
 "confidence": "high|medium|low"}
confidence rubric (judge the ticket text only):
 high   = the ticket names what changes and the expected effect (e.g. "permit icmp from 10.1.10.0/24 to the NOC; nothing else changes")
 medium = the ticket states a goal but no exact objects (e.g. "Let staff ping the NOC")
 low    = the ticket is only an ID or a reference (e.g. "CHG-4412", "fix ticket #881", "per change request 7"); then
          ticket_says = "nothing", expected_effects = [] and you must NOT guess the purpose from the diff."""

PLAN_SYS = """You are the test planner of a network change-validation pipeline. A digital twin compared the network
before and after the change and listed every behavioural difference (impact entries I1, I2, ...).
Your job:
1. For EVERY impact entry decide whether the TICKET explains it:
   "intended"   - the ticket asks for exactly this effect (same hosts/subnets, same service, same direction)
   "unintended" - the ticket does not ask for it, asks for the opposite, or the entry is BROADER than asked
                  (e.g. ticket allows only icmp but the entry opens svc ALL; ticket concerns staff but the entry is
                  about finance hosts; ticket says reachability must not change but a flow changed)
   "unsure"     - the ticket gives no purpose (only an ID) or is too vague to decide
   The diff is NOT evidence of intent: it may contain the mistake we are looking for.
   Entries of kind load / mtu_change / timer_change are compiled into safety tests automatically; just label them.
   Entries of kind path: "intended" if the ticket asks to move/steer/drain traffic, otherwise "unintended".
   VLAN moves: flow entries of the MOVED host are "intended" when the ticket says it should get the access of its new
   VLAN/group; then add one {"t":"like","host":<moved>,"as":<a host already in that VLAN>} test instead of guessing.
   Only write reach expectations the ticket states explicitly; never guess the access of services it does not mention.
2. Add extra tests (typed DSL) for every concrete effect the ticket PROMISES, whether or not it appears in the impact
   list (e.g. "staff1 can ping noc1" -> reach allow; "route must appear on peer1" -> route present; "goes via X" ->
   path via X true; "9000-byte frames pass between a and b" -> mtu size 9000 pass; "link drained" -> load max_pct 0).
   If the ticket promises nothing concrete, add no extra tests. At most 12 extra tests; do not repeat impact entries.
Example: ticket "allow icmp from staff to noc1" with entries I1 flow staff1->noc1 svc icmp deny->allow and
I2 flow staff1->noc1 svc ALL deny->allow -> {"labels":{"I1":"intended","I2":"unintended"},"extra_tests":[{"t":"reach","src":"staff1","dst":"noc1","svc":"icmp","expect":"allow"}]}
Example: ticket "CHG-5521" with entry I1 flow a->b svc tcp/443 allow->deny -> {"labels":{"I1":"unsure"},"extra_tests":[]}
{dsl}
Reply with JSON only: {"labels": {"I1": "intended|unintended|unsure", ...}, "extra_tests": [ ... ]}"""

PLAN_NOIMPACT_SYS = """You are the test planner of a network change-validation pipeline. Given the change ticket, the
intent analysis and the configuration diff, write a change-specific test plan that (a) checks every effect the ticket
promises and (b) checks that nothing else that the diff could affect has changed (use expect "unchanged").
Also consider emulation-only risks: MTU (mtu probes), failover timers (conv), link load after path changes (load).
{dsl}
Reply with JSON only: {"tests": [ ... ]}"""

ONESHOT_SYS = """You are a network engineer. Write a test plan for the network change below, in the typed test
language. The tests will be executed on a digital twin; if all pass, the change is approved.
{dsl}
Reply with JSON only: {"tests": [ ... ]}"""


# =========================================================================== helpers
def _short(x, n=110):
    s = json.dumps(x) if not isinstance(x, str) else x
    return s if len(s) <= n else s[:n] + "..."


def render_diff(sd: List[dict], limit=40) -> str:
    out = []
    for x in sd[:limit]:
        if "changed" in x:
            out.append(f"{x['device']} | inventory | " + _short(x["changed"], 160))
        else:
            for l in x["removed"]:
                out.append(f"{x['device']} | {x['section']} | - {l}")
            for l in x["added"]:
                out.append(f"{x['device']} | {x['section']} | + {l}")
    return "\n".join(out)


def render_impact(ents: List[dict], limit=45) -> str:
    out = []
    for e in ents[:limit]:
        k = e["kind"]
        if k == "flow":
            svc = "ALL (icmp, tcp/22, tcp/25, tcp/80, tcp/443, tcp/5432, udp/53)" if e["svc"] == "*" else e["svc"]
            s = f"{e['id']} flow {e['src']} -> {e['dst']} svc {svc}: {e['before']} -> {e['after']}"
            if e.get("why"):
                s += f" [{e['why']}]"
        elif k == "route":
            s = f"{e['id']} route {e['prefix']}: added on {e['added_on']} removed on {e['removed_on']}"
        elif k == "adj":
            s = f"{e['id']} adjacency {e['a']}--{e['b']}: {e['before']} -> {e['after']} {e.get('why','')}"
        elif k == "path":
            s = f"{e['id']} path {e['src']} -> {e['dst']}: {len(e['before'])} path(s) {e['before'][:2]} -> {len(e['after'])} path(s) {e['after'][:2]}"
        elif k == "load":
            s = f"{e['id']} load: link {e['a']}--{e['b']} {e.get('detail', '')} (demands {e['new_demands'][:4]})"
        elif k == "mtu_change":
            s = f"{e['id']} mtu_change on link {e['a']}--{e['b']}: {e['detail']}"
        elif k == "timer_change":
            s = f"{e['id']} timer_change on link {e['a']}--{e['b']}: {e['detail']}"
        else:
            s = f"{e.get('id')} {k}: {e.get('detail','')}"
        out.append(s)
    if len(ents) > limit:
        out.append(f"... and {len(ents) - limit} more entries of the same kinds")
    return "\n".join(out)


def invariant_text(s: Snapshot) -> str:
    p = s.policy["invariants"]
    return ("Network invariants (always checked): no forwarding loops; management host "
            f"{p['mgmt_reachable']['src']} reaches every managed router; isolation pairs must stay blocked: "
            f"{p['isolation']}; external peers {p['no_leak']['externals']} must not learn {p['no_leak']['forbidden']}; "
            f"SLAs: failover <= {s.policy['sla']['convergence_max_s']} s, link utilisation <= {s.policy['sla']['link_util_max_pct']}%.")


def names_text(s: Snapshot) -> str:
    hosts = ", ".join(f"{h.name}({h.addr},{h.group},mtu {h.mtu})" for h in s.hosts.values())
    routers = ", ".join(n for n, d in s.devices.items() if d.kind == "router")
    return f"Hosts: {hosts}\nRouters: {routers}"


def compile_entry(e: dict, label: str, tpost: Twin, tpre: Twin) -> List[dict]:
    """Deterministically turn a labelled impact entry into DSL tests."""
    k = e["kind"]
    if k in ("load",):
        return [{"t": "load", "a": e["a"], "b": e["b"], "max_pct": SLA_UTIL}]
    if k == "timer_change":
        return [{"t": "conv", "a": e["a"], "b": e["b"], "max_s": SLA_CONV}, {"t": "adj", "a": e["a"], "b": e["b"], "expect": "unchanged"}]
    if k == "mtu_change":
        tests = [{"t": "adj", "a": e["a"], "b": e["b"], "expect": "unchanged"}]
        pair = _host_pair_across(tpre, e["a"], e["b"])
        if pair:
            tests.append({"t": "mtu", "src": pair[0], "dst": pair[1], "size": 1500, "expect": "pass"})
        return tests
    if label == "unsure":
        return []
    if k == "flow":
        return [{"t": "reach", "src": e["src"], "dst": e["dst"], "svc": e["svc"],
                 "expect": e["after"] if label == "intended" else "unchanged"}]
    if k == "route":
        dev = (e["added_on"] or e["removed_on"])[0]
        if label == "intended":
            return [{"t": "route", "dev": dev, "prefix": e["prefix"], "expect": "present" if e["added_on"] else "absent"}]
        return [{"t": "route", "dev": dev, "prefix": e["prefix"], "expect": "unchanged"}]
    if k == "adj":
        if e.get("why") == "bgp session":
            return []
        return [{"t": "adj", "a": e["a"], "b": e["b"], "expect": e["after"] if label == "intended" else "unchanged"}]
    if k == "path":
        if label == "intended":
            return [{"t": "reach", "src": e["src"], "dst": e["dst"], "svc": "icmp", "expect": "unchanged"}]
        old_only = [h for h in (e["before"][0] if e["before"] else []) if all(h not in p for p in e["after"])]
        if old_only:
            return [{"t": "path", "src": e["src"], "dst": e["dst"], "via": old_only[0], "expect": True}]
        return []
    return []


def _host_pair_across(tw: Twin, a: str, b: str):
    """A host pair whose (pre-change) path crosses link a--b, preferring telemetry demands."""
    def crosses(x, y):
        r = tw.reach(x, y, "icmp")
        return r["disp"] == "ACCEPTED" and any(a in p and b in p and abs(p.index(a) - p.index(b)) == 1 for p in r["paths"])
    for t in tw.s.traffic:
        if crosses(t[0], t[1]):
            return t[0], t[1]
    eps = tw.endpoints()
    for x in eps:
        for y in eps:
            if x != y and crosses(x, y):
                return x, y
    return None


# =========================================================================== result record
@dataclass
class Verdict:
    verdict: str                       # approve | block | escalate
    reason: str
    tests: List[dict] = field(default_factory=list)
    results: List[dict] = field(default_factory=list)
    llm_calls: int = 0
    llm_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_seconds: float = 0.0
    compute_seconds: float = 0.0
    replans: int = 0
    coverage: float = 0.0
    trace: List[dict] = field(default_factory=list)

    @property
    def ci_seconds(self):
        return self.tool_seconds + self.llm_seconds

    def to_dict(self):
        d = self.__dict__.copy()
        d["ci_seconds"] = self.ci_seconds
        return d


# =========================================================================== proposed agent
@dataclass
class AgentConfig:
    use_impact: bool = True
    use_feedback: bool = True
    use_invariants: bool = True
    tiers: tuple = ("t1", "t2")
    max_replans: int = 3
    k_hop: int = 2


class AgenticValidator:
    def __init__(self, llm: LLM, cfg: AgentConfig = AgentConfig()):
        self.llm = llm
        self.cfg = cfg

    def _ask(self, v: Verdict, system, user, seed, step):
        out, meta = self.llm.chat(system, user, seed)
        v.llm_calls += 1
        v.llm_seconds += meta["latency"]
        v.tokens_in += meta["in_tokens"]
        v.tokens_out += meta["out_tokens"]
        v.cost_usd += meta["cost_usd"]
        v.trace.append({"step": step, "prompt_chars": len(user), "response": out, "latency": meta["latency"],
                        "cached": meta.get("cached", False)})
        return out if isinstance(out, dict) else {}

    def validate(self, pre: Snapshot, post: Snapshot, ticket: str, seed: int = 1,
                 tpre: Twin = None, tpost: Twin = None) -> Verdict:
        t0 = time.time()
        cfg = self.cfg
        v = Verdict("escalate", "")
        tpre = tpre or Twin(pre)
        tpost = tpost or Twin(post)
        sd = structured_diff(pre, post)
        diff_txt = render_diff(sd)
        # ---- 1. intent analyzer
        ia = self._ask(v, INTENT_SYS, f"CHANGE TICKET:\n{ticket}\n\nCONFIG DIFF (device | section | change):\n{diff_txt}",
                       seed, "intent_analyzer")
        conf = str(ia.get("confidence", "low")).lower()
        # ---- 2. impact analyzer (deterministic, Tier 1 differential)
        ents = impact(pre, post, tpre, tpost, sd)
        behavioural = [e for e in ents if e["kind"] in ("flow", "route", "adj")]
        sg = subgraph(post, changed_devices(sd), cfg.k_hop)
        v.trace.append({"step": "impact_analyzer", "entries": len(ents), "behavioural": len(behavioural),
                        "impact": render_impact(ents, 60)})
        inv_txt = invariant_text(pre) if cfg.use_invariants else ""
        ctx = (f"CHANGE TICKET:\n{ticket}\n\nINTENT ANALYSIS:\n{json.dumps(ia)}\n\nCONFIG DIFF:\n{diff_txt}\n\n"
               f"TOPOLOGY ({cfg.k_hop}-hop around changed devices): devices {sg['devices']}; hosts {sg['hosts']}; "
               f"links {sg['links'][:30]}\n{names_text(pre)}\n{inv_txt}")
        feedback = ""
        labels: Dict[str, str] = {}
        tests: List[dict] = []
        replans = 0
        while True:
            # ---- 3. test planner
            if cfg.use_impact:
                user = ctx + f"\n\nIMPACT ENTRIES (twin differential analysis):\n{render_impact(ents)}" + feedback
                plan = self._ask(v, PLAN_SYS.replace("{dsl}", DSL_DOC), user, seed + 100 * replans, f"test_planner#{replans}")
                raw = plan.get("labels", {}) if isinstance(plan.get("labels"), dict) else {}
                for k2, lab in raw.items():
                    if str(lab).lower() in ("intended", "unintended", "unsure"):
                        labels[str(k2)] = str(lab).lower()
                extra = [t for t in plan.get("extra_tests", []) if isinstance(t, dict)] if isinstance(plan.get("extra_tests"), list) else []
                tests = []
                for e in ents:
                    if e["kind"] == "no_behaviour_change":
                        continue
                    lab = labels.get(e["id"])
                    if lab is None and e["kind"] not in ("load", "mtu_change", "timer_change"):
                        continue
                    tests += compile_entry(e, lab or "unintended", tpost, tpre)
                tests += extra
            else:
                user = ctx + feedback
                plan = self._ask(v, PLAN_NOIMPACT_SYS.replace("{dsl}", DSL_DOC), user, seed + 100 * replans, f"test_planner#{replans}")
                tests = [t for t in plan.get("tests", []) if isinstance(t, dict)] if isinstance(plan.get("tests"), list) else []
            # invariant guard (MeshAgent-style): always appended, not left to the LLM
            plan_tests = tests + ([{"t": "inv", "name": n} for n in INVARIANTS] if cfg.use_invariants else [])
            # dedupe
            seen, uniq = set(), []
            for t in plan_tests:
                k = json.dumps(t, sort_keys=True)
                if k not in seen:
                    seen.add(k)
                    uniq.append(t)
            plan_tests = uniq
            # ---- 4. executor
            ex = run_plan(plan_tests, tpre, tpost, cfg.tiers)
            res = ex["results"]
            # ---- 5. evidence judge (deterministic rules)
            failed = [r for r in res if r["status"] == "fail"]
            errors = [r for r in res if r["status"] == "error"]
            valid = [r["test"] for r in res if r["status"] in ("pass", "fail")]
            rel = [e for e in ents if impact_key(e) is not None and e["kind"] != "path"]
            uncovered = [e for e in rel if not covered(e, valid, tpost)]
            cov = 1.0 if not rel else 1 - len(uncovered) / len(rel)
            unsure = [k2 for k2, lab in labels.items() if lab == "unsure"] if cfg.use_impact else []
            purpose = [r for r in res if r["status"] == "pass" and r["test"]["t"] != "inv"]
            v.tests, v.results, v.coverage = plan_tests, res, round(cov, 3)
            v.tool_seconds = ex["tool_seconds"]
            v.trace.append({"step": f"executor+judge#{replans}", "tests": len(plan_tests), "failed": len(failed),
                            "errors": len(errors), "coverage": round(cov, 3), "unsure": unsure})
            if failed:
                v.verdict = "block"
                v.reason = "; ".join(f"{_short(r['test'], 90)} -> {r['detail'][:90]}" for r in failed[:3])
                break
            if unsure:
                v.verdict, v.reason = "escalate", f"agent unsure whether {unsure[:5]} are intended (ambiguous ticket)"
                break
            if conf == "low" and behavioural:
                v.verdict, v.reason = "escalate", "ticket does not state the purpose but the change alters behaviour"
                break
            missing = []
            if errors:
                missing.append("invalid tests: " + "; ".join(f"{_short(r['test'], 60)} ({r['detail']})" for r in errors[:4]))
            if uncovered:
                if cfg.use_impact:
                    missing.append("impact entries without a label: " + ", ".join(e["id"] for e in uncovered[:20]))
                else:
                    missing.append(f"{len(uncovered)} objects affected by the change are not covered by any test")
            if not purpose and (cfg.use_impact and not [e for e in ents if e["kind"] != "no_behaviour_change"]):
                missing.append("no test confirms that the change does what the ticket asks")
            if not missing:
                v.verdict, v.reason = "approve", f"all {len(plan_tests)} tests passed, coverage {cov:.0%}"
                break
            if not cfg.use_feedback or replans >= cfg.max_replans:
                v.verdict, v.reason = "escalate", "insufficient evidence: " + " | ".join(missing)[:300]
                break
            replans += 1
            feedback = ("\n\nFEEDBACK FROM THE EVIDENCE JUDGE (previous plan rejected, fix it): " + " | ".join(missing) +
                        ("\nReturn labels for ALL listed impact entries." if cfg.use_impact else ""))
        v.replans = replans
        v.compute_seconds = round(time.time() - t0, 3)
        return v


# =========================================================================== B3: one-shot LLM
class OneShotLLM:
    def __init__(self, llm: LLM):
        self.llm = llm

    def validate(self, pre: Snapshot, post: Snapshot, ticket: str, seed: int = 1, tpre=None, tpost=None) -> Verdict:
        tpre, tpost = tpre or Twin(pre), tpost or Twin(post)
        v = Verdict("approve", "")
        user = f"CHANGE TICKET:\n{ticket}\n\nUNIFIED DIFF:\n{unified_diff(pre, post)[:6000]}\n\n{names_text(pre)}"
        out, meta = self.llm.chat(ONESHOT_SYS.replace("{dsl}", DSL_DOC), user, seed)
        v.llm_calls, v.llm_seconds = 1, meta["latency"]
        v.tokens_in, v.tokens_out, v.cost_usd = meta["in_tokens"], meta["out_tokens"], meta["cost_usd"]
        out = out if isinstance(out, dict) else {}
        tests = [t for t in out.get("tests", []) if isinstance(t, dict)] if isinstance(out.get("tests"), list) else []
        ex = run_plan(tests, tpre, tpost)
        v.tests, v.results, v.tool_seconds = tests, ex["results"], ex["tool_seconds"]
        failed = [r for r in ex["results"] if r["status"] == "fail"]
        if failed:
            v.verdict, v.reason = "block", "; ".join(f"{_short(r['test'], 80)} -> {r['detail'][:80]}" for r in failed[:3])
        else:
            v.verdict, v.reason = "approve", f"{len(tests)} tests, none failed"
        v.trace.append({"step": "one_shot", "response": out, "latency": meta["latency"]})
        return v
