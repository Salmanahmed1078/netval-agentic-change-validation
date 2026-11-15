"""Full report content. Numbers come from results/*.json and data/results/*.jsonl (via analyze.py helpers)."""
import json
import os
from collections import Counter

from report.content_static import intro, implementation, dataset
from report.doc import ROOT

P = lambda x: "n/a" if x is None or x != x else f"{100 * x:.0f}%"
P1 = lambda x: "n/a" if x is None or x != x else f"{100 * x:.1f}%"


def pval(p):
    if p is None or p != p:
        return "p n/a"
    return "p<0.001" if p < 0.001 else f"p={p:.3f}"


def collect():
    import analyze as A
    from netval.analysis import rates, bootstrap_ci
    R = json.load(open(os.path.join(ROOT, "results", "all_results.json")))
    D = A.D
    cs = D.cases
    R["ds"] = {"n": len(cs), "unsafe": sum(c["label"] == "unsafe" for c in cs), "safe": sum(c["label"] == "safe" for c in cs),
               "templates": len({c["template"] for c in cs}),
               "families": len({c["variant"] for c in cs if c["label"] == "unsafe"}),
               "test_n": len(D.split("test")), "test_unsafe": sum(c["label"] == "unsafe" for c in D.split("test")),
               "example_tickets": D.by_id["D-ACL-1-S"]["tickets"]}
    loc = 0
    for dp in ("netval", "gui", "ci", "tests", "tools", "report"):
        for f in os.listdir(os.path.join(ROOT, dp)):
            if f.endswith(".py"):
                loc += sum(1 for _ in open(os.path.join(ROOT, dp, f)))
    for f in ("analyze.py", "demo.py", "run.py", "gen_dataset.py"):
        loc += sum(1 for _ in open(os.path.join(ROOT, f)))
    R["loc"] = f"{round(loc, -2):,}"
    import glob
    R["api_cost"] = sum(json.load(open(f))["meta"]["cost_usd"] for f in glob.glob(os.path.join(ROOT, "data/llm_cache/hosted*/*.json")))
    sub = json.load(open(os.path.join(ROOT, "data", "local_subset.json")))
    R["local_ids"] = sub
    loc_rs = [r for r in A.get("agent_local", intents=("clear",)) if r["case"] in sub]
    hos_rs = [r for r in A.get("agent_hosted", intents=("clear",)) if r["case"] in {x["case"] for x in loc_rs}]
    R["local_n"] = len(loc_rs)
    R["local_vs_hosted"] = {"local": rates(loc_rs) if loc_rs else None, "hosted": rates(hos_rs) if hos_rs else None}
    R["rows"] = {(r["method"], r["intent"]): r for r in R.get("main", [])}
    R["A"] = A
    # baseline approvals of unsafe changes (which fault families slip through)
    slip = {}
    for m in ("B1_full_suite", "B2_rule_based", "B4_batfish_only", "agent_hosted", "B3_oneshot_hosted"):
        slip[m] = Counter(r["variant"] for r in A.get(m, intents=("clear",)) if r["label"] == "unsafe" and r["verdict"] == "approve")
    R["slip"] = slip
    return R


def row(R, m, it="clear"):
    return R["rows"].get((m, it))


def build(d, R0):
    R = collect()
    intro(d, R)
    implementation(d, R)
    dataset(d, R)
    baselines(d, R)
    graphs(d, R)
    literature(d, R)
    ablation(d, R)
    discussion(d, R)
    evidence(d, R)
    conclusion(d, R)
    references(d)
    appendix(d, R)


# =========================================================================== 3
def baselines(d, R):
    d.h1("3. Baseline Evaluation")
    d.h2("3.1 Metrics")
    d.table(["Metric", "Definition (computed per change x ticket version)"], [
        ["Fault detection rate", "share of unsafe changes that were blocked or escalated"],
        ["False approval rate", "share of unsafe changes that were approved - our main safety metric (target 0)"],
        ["False block rate", "share of safe changes that were blocked"],
        ["Safe approval (automation) rate", "share of safe changes approved without a human"],
        ["Escalation rate / precision", "share of all changes sent to a human / share of escalations that were really unsafe"],
        ["Correct verdict rate", "unsafe -> block or safe -> approve (escalations count as not automated)"],
        ["Required-test coverage", "share of the expert 'required tests' of the case (stored with every template) that the plan contained"],
        ["Tests executed, CI time", "number of tests run; modelled wall-clock time from PR to verdict = tool time (Table 11) + measured LLM latency"],
        ["Cost", "LLM tokens and USD per change"],
        ["Consistency", "Fleiss' kappa and unanimity of verdicts over the 3 repeated runs"],
    ], "Evaluation metrics (Phase 2, Step 8)", widths=[26, 74], small=True)
    d.table(["Test / phase", "Modelled cost (s)", "Basis"], [
        ["Tier-1 snapshot initialisation (pre + post)", "25", "Batfish snapshot init for 10-20 devices"],
        ["Tier-1 query (one test, or one all-pairs batch per source+service for B1/B2)", "1", "Batfish question latency"],
        ["Tier-2 lab boot + initial convergence", "150", "containerlab + FRR boot of 10-20 nodes"],
        ["Tier-2 probe (ping / traceroute / MTU)", "3", "per probe"],
        ["Tier-2 failover experiment (conv)", "45", "fail link, wait for reconvergence, restore"],
        ["Tier-2 load measurement", "20", "one iperf-style traffic run (all links at once for B1)"],
    ], "CI time-cost model used for every method (same numbers for all, so the comparison is fair)", widths=[52, 16, 32], small=True)
    d.h2("3.2 Baselines")
    d.bullets([
        "<b>B1 Fixed full suite</b>: every change runs the same suite - all host pairs x 7 services (reachability vs the pre-change "
        "golden state), every route on every router, every adjacency, the 4 invariants, 1500-byte probes between all reachable "
        "host pairs, failover of every redundant link and the utilisation of every link. Any deviation fails the pipeline.",
        "<b>B2 Rule-based selection</b>: if/else rules map the changed config section (ACL, static route, OSPF, BGP/prefix-list/"
        "route-map, VLAN/host inventory, MTU, NAT, redistribution) to a fixed bundle of tests around the changed device, plus the "
        "invariants; any deviation blocks.",
        "<b>B3 One-shot LLM</b>: the same hosted model, one prompt with the ticket, the raw unified diff and the host/router names; "
        "it writes a DSL test plan with no impact analysis, no invariants and no feedback. The plan is executed; any failure blocks, "
        "otherwise approve (the 'monolithic' setup of CORNETTO [6]).",
        "<b>B4 Batfish-only</b>: no LLM and no emulation - Tier-1 invariants plus the full Tier-1 differential. Invariant failure "
        "blocks; any behavioural difference escalates (it cannot tell intended from unintended); no difference approves.",
    ])
    A = R["A"]
    ms = ["B1_full_suite", "B2_rule_based", "B4_batfish_only", "B3_oneshot_hosted", "agent_hosted"]
    rows = []
    for m in ms:
        r = row(R, m)
        if not r:
            continue
        ci = lambda k: f"{P(r[k])} [{P(r[k + '_ci'][0])}-{P(r[k + '_ci'][1])}]"
        rows.append([A.NAMES[m], ci("detection"), ci("false_approval"), ci("false_block"), P(r["escalation_rate"]),
                     P(r["safe_approval"]), P(r["required_coverage"]), f"{r['tests']:.0f}", f"{r['ci_seconds'] / 60:.1f}"])
    d.table(["Method", "Detection", "False approval", "False block", "Escal.", "Safe auto-approved", "Req. cov.", "Tests", "CI min"],
            rows, f"Baselines vs. the proposed agent on the test split ({R['ds']['test_n']} changes, clear tickets), "
            "95% bootstrap confidence intervals in brackets", widths=[22, 13, 13, 13, 7, 9, 7, 7, 9], small=True)
    b1, b2, b4, b3 = (row(R, m) for m in ("B1_full_suite", "B2_rule_based", "B4_batfish_only", "B3_oneshot_hosted"))
    s1 = R["slip"]["B1_full_suite"]
    s4 = R["slip"]["B4_batfish_only"]
    d.h2("3.3 What the baselines tell us")
    d.p(f"<b>B1 is the coverage upper bound, but it is blind to intent.</b> It detects {P(b1['detection'])} of unsafe changes and needs "
        f"on average {b1['tests']:.0f} tests and {b1['ci_seconds'] / 60:.0f} modelled minutes per change. It still approves "
        f"{P(b1['false_approval'])} of the unsafe changes: those are changes that <i>do nothing</i> or do the wrong thing without "
        f"breaking anything that was true before ({', '.join(k.replace('_', ' ') for k, _ in s1.most_common(4))}). A golden-state suite "
        f"cannot know that the ticket asked for a new route or a new permit. The other side is worse: it blocks "
        f"{P(b1['false_block'])} of the <i>safe</i> changes, because every intended behaviour change is a deviation from the golden "
        f"state. In practice an engineer would have to update the suite by hand for most PRs.")
    d.p(f"<b>B2 is cheap but misses side effects.</b> Rules around the changed device cut the suite to {b2['tests']:.0f} tests, "
        f"but detection falls to {P(b2['detection'])} and false approvals rise to {P(b2['false_approval'])}: faults whose effect "
        f"shows up away from the changed device (overloaded backup paths, leaks seen only by the peer, failover loops) are outside "
        f"the bundle. It still blocks {P(b2['false_block'])} of safe changes for the same reason as B1.")
    d.p(f"<b>B4 never blocks a safe change, but it is not a gate.</b> It escalates {P(b4['escalation_rate'])} of all changes "
        f"(it cannot tell intended from unintended differences) and approves {P(b4['false_approval'])} of the unsafe ones - every fault "
        f"that has no static footprint ({', '.join(k.replace('_', ' ') for k, _ in s4.most_common(5))}). This is the "
        f"CORNETTO [6] observation in our setting: static verification is strong but cannot see timing, packet size or load.")
    if b3:
        b3t = row(R, "B3_oneshot_hosted", "terse")
        d.p(f"<b>B3 (one-shot LLM) shows that the model alone is not the contribution.</b> With the same model but no impact analysis, "
            f"invariants, evidence rules or feedback it writes {b3['tests']:.0f} tests per change and, on clear tickets, detects "
            f"{P(b3['detection'])} of unsafe changes - but it blocks {P(b3['false_block'])} of the <i>safe</i> ones, because many of "
            f"its own expectations are wrong (it guesses access rules the ticket never mentioned). It also has no way to say 'I am "
            f"not sure': with terse tickets it approves {P(b3t['false_approval'])} of unsafe changes, where the full agent "
            f"approves none. A one-shot plan is a list of guesses; the agent's plan is anchored to what actually changed.")
    d.fig("../figures/fig_confusion.png", "Confusion matrices (rows = oracle label, columns = verdict) on clear tickets.", 98)


# =========================================================================== 4
def graphs(d, R):
    a = row(R, "agent_hosted")
    b1 = row(R, "B1_full_suite")
    d.h1("4. Graphical Results and Visualization")
    d.p("All figures are produced by <code>analyze.py</code> from the logged runs on the test split; error bars are 95% bootstrap "
        "intervals that resample whole change cases.")
    d.fig("../figures/fig_main_rates.png", "Detection, false approval, false block and escalation rates per method (clear tickets; the local agent on its 32-case subset).", 100)
    d.p(f"Figure 5 is the main result. The proposed agent detects {P(a['detection'])} of unsafe changes with a false-approval rate of "
        f"{P1(a['false_approval'])} while blocking only {P(a['false_block'])} of safe changes and escalating {P(a['escalation_rate'])}. "
        f"It is the only method that is good on both axes at once: B1 matches it on neither, B4 avoids false blocks only by "
        f"escalating most changes.")
    d.fig("../figures/fig_time_vs_detection.png", "Left: detection vs modelled CI time per change. Right: safety (1 - false approval) vs "
          "number of tests executed. Up-and-left is better.", 100)
    d.p(f"Figure 6 answers the efficiency half of RQ1. The agent runs {a['tests']:.0f} tests on average "
        f"({b1['tests'] / max(a['tests'], 1):.0f}x fewer than B1) and needs {a['ci_seconds'] / 60:.1f} modelled minutes instead of "
        f"{b1['ci_seconds'] / 60:.0f} ({b1['ci_seconds'] / max(a['ci_seconds'], 1):.1f}x faster), including "
        f"{a['llm_seconds']:.0f} s of LLM time. Most of the agent's remaining time is the Tier-2 lab boot, which is only paid "
        f"when a Tier-2 test is needed.")
    d.fig("../figures/fig_tests_time_box.png", "Distribution of tests executed and modelled CI time per change (log scale).", 100)
    d.fig("../figures/fig_per_class.png", "Per change class: correct-verdict rate (left) and false-approval rate (right), clear tickets (local agent: 32-case subset).", 100)
    A = R["A"]
    from collections import Counter as _C
    fa = {m: _C(r["cls"] for r in A.get(m, intents=("clear",)) if r["label"] == "unsafe" and r["verdict"] == "approve")
          for m in ("B4_batfish_only", "B2_rule_based", "agent_hosted")}
    err = _C(r["cls"] for r in A.get("agent_hosted", intents=("clear",))
             if (r["label"] == "unsafe" and r["verdict"] == "approve") or (r["label"] == "safe" and r["verdict"] != "approve"))
    fmt = lambda c: ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in c.most_common())
    d.p(f"Figure 8 breaks the results down by change class. B4's false approvals ({fmt(fa['B4_batfish_only'])}) come from two "
        f"sources: faults with no static footprint (OSPF timers and SPF delay, MTU typos, overloaded backup paths, failover loops) and "
        f"changes that simply do nothing (a shadowed rule, a wrong port) - B4 has no notion of what should have changed. B2's misses "
        f"({fmt(fa['B2_rule_based'])}) are spread over five classes, wherever the fault shows up away from the changed device. The "
        f"agent's {sum(err.values())} non-correct verdicts on clear tickets are {fmt(err)}; its only false approval is a static "
        f"route (Section 7.4).")
    d.fig("../figures/fig_intent_quality.png", "Effect of ticket quality (clear / vague / terse) on the LLM-based methods.", 100)
    av, at = row(R, "agent_hosted", "vague"), row(R, "agent_hosted", "terse")
    d.p(f"Figure 9 is the RQ3 experiment. Going from clear to vague tickets, the agent's detection drops from {P(a['detection'])} "
        f"to {P(av['detection'])} and false approvals rise from {P1(a['false_approval'])} to {P1(av['false_approval'])}: with only a "
        f"goal, the planner sometimes accepts a side effect as part of that goal. With terse tickets (only an ID) the agent behaves "
        f"as designed - it approves nothing ({P(at['safe_approval'])} of safe changes auto-approved), escalates "
        f"{P(at['escalation_rate'])} of all changes and still blocks the unsafe changes whose failures are intent-independent "
        f"(invariants, MTU, load, failover), so its false-approval rate is {P1(at['false_approval'])}. Ticket quality decides how "
        f"much can be automated, not how safe the gate is.")
    d.fig("../figures/fig_tiers.png", "Which twin tier produced the evidence that blocked each unsafe change (agent, clear tickets).", 55)
    tt = R.get("tiers", {})
    ov = tt.get("overall", {})
    d.p(f"Figure 10 shows which tier did the work (RQ4). Tier 1 alone produced the blocking evidence for "
        f"{ov.get('Tier 1 (static)', 0)} unsafe changes, Tier 2 alone for {ov.get('Tier 2 (emulation)', 0)} and both for "
        f"{ov.get('both', 0)}. Independently, {tt.get('t1_invisible_unsafe', 0)} unsafe test cases leave no trace at all in a "
        f"static analysis (B4 approves them) - those are exactly the faults that justify the emulation tier.")
    d.fig("../figures/fig_cost.png", "LLM time, tokens and API cost per change (clear tickets).", 100)
    d.p(f"Figure 11 shows the price of the LLM part: on average {a['tokens']:.0f} tokens and ${a['cost_usd']:.3f} per change for the "
        f"hosted model, i.e. about ${a['cost_usd'] * 1000:.0f} per 1000 pull requests - negligible next to engineer time.")


# =========================================================================== 5
def literature(d, R):
    a = row(R, "agent_hosted")
    av = row(R, "agent_hosted", "vague")
    at = row(R, "agent_hosted", "terse")
    lv = R["local_vs_hosted"]
    d.h1("5. Comparison with Existing Research")
    d.p("No paper from Phases 1-2 evaluates the same task on the same data, so a perfectly fair head-to-head is impossible. "
        "Table 13 lines up the closest comparable metric of each paper with ours and states what limits the comparison.")
    d.table(["Paper", "Their task / data", "Their reported result", "Our comparable result", "Comparability limits"], [
        ["[1] Aether (2026)", "Agentic change validation, 8 synthetic scenarios + 2 ISP incidents", "94% faulty-change detection; 6-7 min/change",
         f"{P(a['detection'])} detection on {R['ds']['test_unsafe']} unsafe test changes; {a['ci_seconds'] / 60:.1f} modelled min/change",
         "Different networks and twin; Aether used real Batfish/NS-3 and wall-clock time; no fixed-suite baseline in [1]"],
        ["[4] NetArena (2026)", "Agents fixing networks in emulators", "avg. correctness ~24%, best < 60%; some agents correct but unsafe",
         f"false approval {P1(a['false_approval'])} (safety measured separately, as [4] recommends)",
         "Fixing is harder than judging; we judge someone else's change"],
        ["[5] Cleverest (2026)", "LLM regression tests from commit intent", "20 bugs with intent vs 4 without",
         f"detection {P(a['detection'])} clear / {P(av['detection'])} vague; terse: 0 approvals, {P(at['escalation_rate'])} escalated",
         "Software commits vs network changes; same direction: intent quality drives test quality"],
        ["[6] CORNETTO (2026)", "Agentic config repair with Batfish", "regressions 34% -> <6% (agent vs one-shot); 1.3% with verifier feedback",
         f"false approval {P1(a['false_approval'])} (agent) vs {P1(row(R, 'B3_oneshot_hosted')['false_approval'])} (one-shot B3, which also "
         f"blocks {P(row(R, 'B3_oneshot_hosted')['false_block'])} of safe changes; terse tickets: 0% vs {P(row(R, 'B3_oneshot_hosted', 'terse')['false_approval'])})",
         "Repair vs validation; their regressions are new spec violations, ours are approved unsafe changes"],
        ["[7] NetIntent / [8] LLNet (2025)", "Intent translation with local models", "86-100% correct translation with few-shot prompts",
         (f"local 7B agent: {P(lv['local']['detection'])} detection, {P1(lv['local']['false_approval'])} false approval on "
          f"{R['local_n']} cases" if lv.get("local") else "local subset still running"),
         "Translation accuracy vs gate verdicts; small datasets on both sides"],
        ["[9] MeshAgent (2025)", "Invariant-guided reliable management", ">95% accuracy, abstention improves reliability",
         f"invariant guard + escalation: {P(at['escalation_rate'])} abstention on terse tickets, 0 terse approvals",
         "Only headline numbers of [9] are public"],
        ["[11] Ding et al. (2025)", "AutoML twin for latency", "500x faster than Mininet, latency only",
         "Tier-2 checks only when a T2-relevant impact exists", "Different purpose: their twin predicts, ours verifies"],
    ], "Comparison with the papers of Phases 1-2", widths=[13, 20, 20, 25, 22], small=True)
    d.fig("../figures/fig_literature.png", "Our agent vs. Aether's reported detection rate and time per change (different settings - "
          "indicative only).", 85)
    d.p("Read with these limits, our results are in line with the literature: like Aether we see high detection of faulty "
        "changes in minutes; like NetArena and CORNETTO we find that correctness and safety have to be measured separately and "
        "that verifier-grounded agents beat one-shot prompting; like Cleverest, intent text quality is the main driver of how much "
        "an LLM-generated test plan is worth. What none of them measured, and we did, is the comparison with a fixed CI suite "
        "on the same changes, with false approval as the main metric.")


# =========================================================================== 6
def ablation(d, R):
    rows = R.get("ablation", [])
    d.h1("6. Ablation Studies")
    d.p("We removed or replaced one component at a time and re-ran the test split with clear tickets (Phase 2, Step 10). The tier "
        "ablations re-execute the same plans with one tier disabled, so they isolate the twin from the LLM.")
    full = next((r for r in rows if r["label"] == "Full agent"), None)
    tr = []
    for r in rows:
        dd = lambda k: "" if not full or r is full else f" ({'+' if r[k] - full[k] >= 0 else ''}{100 * (r[k] - full[k]):.0f})"
        tr.append([r["label"], P(r["detection"]) + dd("detection"), P1(r["false_approval"]) + dd("false_approval"),
                   P(r["false_block"]) + dd("false_block"), P(r["safe_approval"]) + dd("safe_approval"),
                   P(r["required_coverage"]), f"{r['tests']:.0f}", f"{r['ci_seconds'] / 60:.1f}"])
    d.table(["Variant", "Detection", "False approval", "False block", "Safe auto-approved", "Req. cov.", "Tests", "CI min"], tr,
            "Ablation results (test split, clear tickets unless stated; change vs. full agent in percentage points)",
            widths=[24, 11, 12, 11, 12, 9, 8, 8], small=True)
    d.fig("../figures/fig_ablation.png", "Ablations: detection, false approval and automation rate per variant.", 100)
    g = {r["label"]: r for r in rows}
    txt = []
    if full and "- impact analyzer" in g:
        x = g["- impact analyzer"]
        txt.append(f"<b>Impact analyzer.</b> Without the blast radius the planner has to guess from the ticket and the raw diff "
                   f"what could be affected, and writes {x['tests']:.0f} tests instead of {full['tests']:.0f}. Detection barely moves "
                   f"({P(full['detection'])} -> {P(x['detection'])}) and false approvals rise from {P1(full['false_approval'])} to "
                   f"{P1(x['false_approval'])}, but false blocks explode from {P(full['false_block'])} to {P(x['false_block'])}: the "
                   f"planner asserts 'unchanged' on behaviour the change legitimately alters, or guesses expectations that are wrong. "
                   f"Safe auto-approval halves ({P(full['safe_approval'])} -> {P(x['safe_approval'])}) and CI time grows to "
                   f"{x['ci_seconds'] / 60:.1f} min. This is the component that matters most.")
    if full and "- feedback loop" in g:
        x = g["- feedback loop"]
        if abs(x["safe_approval"] - full["safe_approval"]) < 1e-9 and abs(x["false_approval"] - full["false_approval"]) < 1e-9:
            nimp = g.get("- impact analyzer", {})
            txt.append(f"<b>Feedback loop.</b> Removing it changes nothing: in all {full.get('n_all', 0)} runs of the full agent the "
                       f"first plan was already complete (0 replans), so the loop never fired with the hosted model. It only "
                       f"matters when plans are incomplete: without the impact analyzer it was needed in "
                       f"{nimp.get('runs_with_replans', 0)} of {nimp.get('n_all', 0)} runs. We keep it as a cheap safety valve, "
                       f"but on our data it is not a contributor.")
        else:
            txt.append(f"<b>Feedback loop.</b> Without replanning, incomplete plans are escalated instead of fixed: safe auto-approval "
                       f"{P(full['safe_approval'])} -> {P(x['safe_approval'])}, false approvals {P1(full['false_approval'])} -> "
                       f"{P1(x['false_approval'])}.")
    if full and "- invariant guard" in g:
        x = g["- invariant guard"]
        txt.append(f"<b>Invariant guard.</b> Removing the automatically appended invariant tests (and the invariant text in the prompt) "
                   f"changes detection {P(full['detection'])} -> {P(x['detection'])} and false approvals {P1(full['false_approval'])} -> "
                   f"{P1(x['false_approval'])}. Most invariant violations also appear as unintended impact entries, so the guard is "
                   f"mainly a safety net for entries the LLM mislabels.")
    if full and "Tier 1 only (static)" in g and "Tier 2 only (emulation)" in g:
        x, y = g["Tier 1 only (static)"], g["Tier 2 only (emulation)"]
        txt.append(f"<b>Twin tiers.</b> With Tier 1 only, the Tier-2 tests (MTU probes, failover, load) are skipped, so the impact "
                   f"entries that need them stay without evidence. The judge then <i>escalates</i> instead of approving: false "
                   f"approvals stay at {P1(x['false_approval'])} (full: {P1(full['false_approval'])}), but escalations rise from "
                   f"{P(full['escalation_rate'])} to {P(x['escalation_rate'])} and safe auto-approval drops from "
                   f"{P(full['safe_approval'])} to {P(x['safe_approval'])}; CI time falls to {x['ci_seconds'] / 60:.1f} min. In other "
                   f"words the evidence rule turns a missing tier into human work, not into risk. With Tier 2 only, every run pays "
                   f"the lab boot ({y['ci_seconds'] / 60:.1f} min instead of {full['ci_seconds'] / 60:.1f}), formal ACL questions "
                   f"cannot be answered and wildcard services are only sampled: detection {P(y['detection'])}, false approvals "
                   f"{P1(y['false_approval'])}, false blocks {P(y['false_block'])}, safe auto-approval {P(y['safe_approval'])}. "
                   f"Both tiers are needed: Tier 1 for exhaustive, cheap checks; Tier 2 for evidence static analysis cannot give.")
    for k in ("vague ticket (not clear)", "terse ticket (not clear)"):
        if k in g:
            x = g[k]
            txt.append(f"<b>{k.split(' (')[0].capitalize()}.</b> Detection {P(x['detection'])}, false approval {P1(x['false_approval'])}, "
                       f"safe auto-approval {P(x['safe_approval'])} (Section 4, Figure 9).")
    if "local 7B model (32-case subset)" in g:
        lv = R["local_vs_hosted"]
        lo, ho = lv["local"], lv["hosted"]
        txt.append(f"<b>Local vs hosted model.</b> On the {R['local_n']}-case stratified subset (clear tickets) the local Qwen2.5-7B "
                   f"agent reaches {P(lo['detection'])} detection with <b>{P(lo['false_approval'])} false approvals</b> and "
                   f"{P(lo['false_block'])} false blocks, against {P(ho['detection'])} / {P(ho['false_approval'])} / {P(ho['false_block'])} "
                   f"for the hosted model on the same cases, and it needs about {lo['llm_seconds']:.0f} s of LLM time per change on our "
                   f"laptop (hosted: {ho['llm_seconds']:.0f} s). The deterministic parts do not rescue a weak planner: the 7B model "
                   f"mostly forgets to test effects the ticket promises (a VIP route that should appear, a link that should be "
                   f"drained), so changes that silently do nothing pass. With today's small models the privacy-preserving option "
                   f"is only usable in an 'escalate, never auto-approve' mode.")
    d.bullets(txt)


# =========================================================================== 7
def discussion(d, R):
    a, av, at = row(R, "agent_hosted"), row(R, "agent_hosted", "vague"), row(R, "agent_hosted", "terse")
    st = R.get("stats", {})
    cons = R.get("consistency", {}).get("agent_hosted", {})
    d.h1("7. Results Analysis and Discussion")
    d.h2("7.1 Answers to the research questions")
    rows = []
    for b in ("B1_full_suite", "B2_rule_based", "B4_batfish_only", "B3_oneshot_hosted", "agent_local"):
        if b not in st:
            continue
        s = st[b]
        rows.append([R["A"].NAMES[b], s["n_pairs"],
                     f"{s['detection']['a_only']} / {s['detection']['b_only']}, {pval(s['detection'].get('p_holm'))}",
                     f"{s['false_approval']['a_only']} / {s['false_approval']['b_only']}, {pval(s['false_approval'].get('p_holm'))}",
                     f"{s['false_block']['a_only']} / {s['false_block']['b_only']}, {pval(s['false_block'].get('p_holm'))}",
                     f"{s['tests']['median_diff']:+.0f}, {pval(s['tests'].get('p_holm'))}",
                     f"{s['time']['median_diff']:+.0f} s, {pval(s['time'].get('p_holm'))}"])
    d.table(["Agent vs.", "Pairs", "Detection (agent-only / base-only)", "False approval (agent / base)", "False block (agent / base)",
             "Tests (median diff)", "CI time (median diff)"], rows,
            "Paired tests over all ticket versions: exact McNemar for verdict outcomes, Wilcoxon signed-rank for tests/time; "
            "p-values Holm-corrected", widths=[18, 7, 17, 16, 16, 13, 13], small=True)
    b1 = row(R, "B1_full_suite")
    d.p(f"<b>RQ1 - Do change-specific tests catch as many unsafe changes as a fixed suite, with fewer tests and less time?</b> "
        f"Yes. On clear tickets the agent detects {P(a['detection'])} of unsafe changes against {P(b1['detection'])} for the full suite, "
        f"with {a['tests']:.0f} instead of {b1['tests']:.0f} tests and {a['ci_seconds'] / 60:.1f} instead of {b1['ci_seconds'] / 60:.0f} "
        f"modelled minutes. The differences in detection, tests and time are significant after Holm correction (Table 15). "
        f"The fixed suite still has one advantage: it tests failover of every link, so it catches the static-pin micro-loops that "
        f"our agent misses (Section 7.4).")
    d.p(f"<b>RQ2 - How often does the agent approve an unsafe change, and do evidence rules plus escalation reduce that to zero?</b> "
        f"Not quite to zero. The false-approval rate is {P1(a['false_approval'])} on clear, {P1(av['false_approval'])} on vague and "
        f"{P1(at['false_approval'])} on terse tickets, against {P(b1['false_approval'])} for B1 and "
        f"{P(row(R, 'B4_batfish_only')['false_approval'])} for B4. The remaining false approvals are all of one kind (failover "
        f"risk that no impact entry points to) or caused by vague tickets; the rules themselves never let a failing test or a "
        f"violated invariant through. Escalations are useful: their precision is {P(a['escalation_precision'])} on clear tickets.")
    d.p(f"<b>RQ3 - How much do detection and escalation change with vague or terse tickets?</b> A lot, in the way we hoped. Vague "
        f"tickets cost detection ({P(a['detection'])} -> {P(av['detection'])}) and add false blocks ({P(a['false_block'])} -> "
        f"{P(av['false_block'])}); terse tickets switch the agent into 'safe mode': {P(at['escalation_rate'])} escalations, no "
        f"approvals, unsafe changes with intent-independent faults still blocked. The intent analyzer's confidence rubric and the "
        f"'unsure' label are what make this work.")
    d.p("<b>RQ4 - Which tier and which components matter most?</b> The impact analyzer (blast radius) matters most for both detection "
        "and automation; Tier 2 is essential for safety (MTU, timers, overload, failover), Tier 1 for exhaustive formal checks; "
        "the feedback loop mainly increases automation; the invariant guard is a safety net (Section 6).")
    if cons:
        d.p(f"<b>Consistency.</b> Over 3 repeats per change x ticket, Fleiss' kappa is {cons.get('fleiss_kappa', float('nan')):.2f} and "
            f"{P(cons.get('unanimous'))} of the verdicts are identical in all repeats; majority voting changes the rates by at most "
            f"a few points. A deterministic judge on top of the LLM keeps the gate stable.")
    d.h2("7.2 Does this address the research gap?")
    d.p("The Phase-2 gap had four parts. Gap 1 (no comparison of intent-aware selection vs a fixed suite on the same changes): "
        "addressed - B1-B4 and the agent ran on identical changes and snapshots. Gap 2 (false approval and escalation not "
        "measured): addressed - false approval is our primary metric, with escalation rate and precision. Gap 3 (effect of intent "
        "quality unknown): addressed with three ticket versions per change. Gap 4 (which twin fidelity is enough): addressed by "
        "recording the tier of each piece of evidence and by the tier ablation. The limitation is that our twin is our own code, so "
        "the absolute numbers are about our lab, not production networks.")
    d.h2("7.3 Strengths and limitations")
    d.bullets([
        "<b>Strength - safety by construction.</b> The LLM only labels and proposes; deterministic code computes the blast radius, "
        "runs the tests and decides. A wrong LLM answer can cost automation; it causes a false approval only if it labels a "
        "harmful side effect 'intended' or forgets to test an effect the ticket promises (the second failure dominates for the 7B model).",
        "<b>Strength - cheap and fast.</b> About 12 tests and two LLM calls per change; cost in cents.",
        "<b>Strength - graceful with bad tickets.</b> Terse tickets escalate rather than approve.",
        "<b>Limitation - twin fidelity.</b> Our Python twin models OSPF, eBGP, ACL/NAT, VLANs, MTU, timers and load, but not "
        "vendor quirks, iBGP/route reflection, multicast, QoS queues or real software bugs. Results on real Batfish/FRR may differ.",
        "<b>Limitation - synthetic changes.</b> 120 generated changes with injected faults from known families (as in [4], [6]); "
        "real tickets are messier and mix several goals.",
        "<b>Limitation - blind spots of the impact analyzer.</b> Risks that do not change any steady-state behaviour (a static pin "
        "that only loops after a failure) produce no impact entry, so nothing prompts a failover test.",
        "<b>Limitation - model dependence.</b> The full grid ran on one hosted model; the local 7B model ran on a 32-case subset only and was "
        "clearly weaker (Section 6), so the privacy argument of Phase 2 is not yet settled.",
        "<b>Threats to validity.</b> Prompts were tuned on the campus split only; the oracle and the agent share the twin, so a "
        "twin bug would affect both (mitigated by the 12 protocol tests); the CI time is modelled, not measured on real tools.",
    ])
    d.h2("7.4 Failure analysis and worked examples")
    fails = R.get("failures", [])
    cnt = Counter(f["category"] for f in fails)
    d.p(f"Every wrong or non-automated verdict of the agent on the test split ({len(fails)} change x ticket pairs over all three "
        f"ticket versions, out of {3 * R['ds']['test_n']}) was put into the Phase-2 failure buckets (Figure 14).")
    d.fig("../figures/fig_failures.png", "Failure taxonomy of the proposed agent.", 80)
    d.table(["Category", "Count", "What happened"], [
        [k, v, {
            "Escalated: ambiguous ticket (by design)": "terse/vague ticket; the agent asked a human instead of guessing - intended behaviour",
            "Missed dependency (Tier-2 risk not flagged by impact analysis)": "the fault only shows on failure (static-pin micro-loop); no impact entry asked for a failover test",
            "Missed dependency / no test for the faulty effect": "the faulty effect had no impact entry and no ticket promise, so no test checked it",
            "Intent misread (side effect labelled intended)": "the planner accepted an unintended behaviour change as part of the goal",
            "Bad test (wrong expectation on intended effect)": "an extra test encoded a wrong expectation (e.g. a path via the wrong router)",
            "Missing intent (vague/terse ticket, no expectation to test)": "nothing in the ticket said what should change, and nothing broke visibly",
            "Escalated: insufficient evidence": "plans still incomplete after 3 replans",
            "Escalated: invalid tests after replans": "DSL errors persisted",
            "Judge rule gap (invariant)": "invariant failed on a safe change",
            "Tool / test error": "executor error",
        }.get(k, "")] for k, v in cnt.most_common()], "Failure buckets", widths=[34, 8, 58], small=True)
    d.p("<b>Example 1 - missed dependency (D-STA-1-F, false approval).</b> The ticket asks to pin traffic from leaf1 towards app-a1 "
        "onto spine1 with a static route. The diff does exactly that and Tier 1 shows only path changes, which the planner "
        "correctly labels intended; all 14 tests pass. But when the leaf2-spine1 link fails silently, spine1 reroutes via other "
        "leaves while leaf1 keeps forcing traffic to spine1: a micro-loop and an infinite outage for web-a1 -> app-a1. Only a "
        "failover test would show it, and no impact entry asked for one. B1 blocks this change because it tests failover of every "
        "link. Fix for future work: a rule 'static route added -> failover tests on the links of the pinned path'.")
    d.p("<b>Example 2 - intent misread with a vague ticket (D-ACL-2-F).</b> Clear ticket: 'add <i>deny tcp 10.101.2.0/24 "
        "10.102.1.0/24 eq 443</i>'; the faulty diff denies <i>all</i> IP from staging to the app. With the clear ticket the agent "
        "labels the extra denied services unintended and blocks. With the vague ticket ('Stop staging from hitting the "
        "production app') blocking everything is a reasonable reading, so the planner labels it intended and approves - the "
        "Clarify [10] ambiguity problem in action.")
    d.p("<b>Example 3 - correct escalation (I-BGP-1-S, terse).</b> The ticket is 'implements CHG-9791'. The intent analyzer returns "
        "confidence 'low' and 'ticket_says: nothing'; the impact analyzer finds a new route 172.16.11.0/24; the planner labels it "
        "'unsure'; the judge escalates. B1 and B2 block this <i>safe</i> change and B4 escalates it too - but B4 would also have "
        "approved the faulty version of other changes that leave no static trace (Figure 21 shows this run in the GUI).")
    d.h2("7.5 Recommended gate configuration")
    d.bullets([
        "Auto-approve only when: no failed test, 100% impact coverage, intent confidence high or medium, and no 'unsure' label.",
        "Always run Tier 2 when the impact includes MTU, timer/BFD/SPF or load-shift entries, or a static route is added "
        "(this last rule would have removed our only clear-ticket false approval).",
        "Treat ID-only tickets as 'escalate unless nothing behavioural changed'; ask authors for one sentence of intent - on our data "
        "that single sentence moves most safe changes from human review to auto-approval.",
        "With a local 7B model, run the gate in 'block or escalate' mode only (never auto-approve) until a stronger local model is validated.",
        "Keep a nightly full-suite run (B1) as a backstop; with the agent on every PR the full suite no longer blocks delivery.",
    ])


# =========================================================================== 8
def evidence(d, R):
    d.h1("8. Implementation Evidence (Code Running, GUI and CI Screenshots)")
    d.p("The screenshots below are taken from the running prototype on our machine (GUI in Chrome; terminal output captured from "
        "real runs).")
    d.fig("../screenshots/term_pytest.png", "Twin verification: 12 protocol-behaviour tests pass.", 88)
    d.fig("../screenshots/term_dataset.png", "Dataset generation with oracle labelling.", 88)
    d.fig("../screenshots/gui_dashboard.png", "NetVal Studio - dashboard with dataset summary and headline result figures.", 100)
    d.fig("../screenshots/gui_cases.png", "NetVal Studio - list of the labelled change cases.", 100)
    d.fig("../screenshots/gui_case_block.png", "Validating a faulty jumbo-frame PR (D-MTU-1-F, clear ticket): the impact analyzer finds "
          "the OSPF adjacency stuck in ExStart; the agent blocks with 17 tests in about 4.5 modelled minutes; B1 needs 1446 tests.", 92)
    d.fig("../screenshots/gui_case_approve.png", "Validating a correct ACL change (D-ACL-1-S): the agent approves; B1 and B2 would have "
          "blocked this safe change and B4 escalates it.", 92)
    d.fig("../screenshots/gui_case_terse.png", "A terse ticket ('implements CHG-9791'): low intent confidence, the new route is labelled "
          "'unsure', and the gate escalates to a human.", 92)
    d.fig("../screenshots/term_demo.png", "Console run of the same pipeline (demo.py) with per-stage output and the baselines.", 100)
    d.fig("../screenshots/term_ci.png", "CI gate on two pull requests: the faulty PR #42 exits with code 1 (pipeline fails), the "
          "correct PR #43 exits with 0.", 100)
    d.fig("../screenshots/gui_ci.png", "The PR comment the CI job posts for the blocked PR (rendered in the GUI).", 90)
    d.fig("../screenshots/gui_results.png", "NetVal Studio - results page.", 100)


def conclusion(d, R):
    a = row(R, "agent_hosted")
    b1 = row(R, "B1_full_suite")
    d.h1("9. Conclusion and Next Steps")
    d.p(f"We implemented the Phase-2 methodology end to end and evaluated it on {R['ds']['n']} labelled network changes with three "
        f"ticket versions each. On clear tickets the intent-aware agent caught {P(a['detection'])} of unsafe changes with "
        f"{P1(a['false_approval'])} false approvals while running {a['tests']:.0f} tests in {a['ci_seconds'] / 60:.1f} modelled minutes, "
        f"where a fixed full suite caught {P(b1['detection'])}, approved {P(b1['false_approval'])} of unsafe changes, blocked "
        f"{P(b1['false_block'])} of safe ones and needed {b1['tests']:.0f} tests and {b1['ci_seconds'] / 60:.0f} minutes. With an "
        f"ID-only ticket the agent never approved anything. The blast-radius analysis and the emulation tier are the two parts "
        f"that matter most.")
    d.p("Next steps for the final phase: (1) add the static-route failover rule and a 'promised effects must be tested' rule and re-run; (2) port Tier 1 to real Batfish and "
        "Tier 2 to containerlab on a Linux machine to check that the numbers hold with real tools; (3) run larger local models (e.g. 14-32B, as in NetIntent [7]) on the "
        "full grid on a machine with more memory; (4) collect a small set of real anonymised change tickets to test on messy intent.")


def references(d):
    d.h1("References")
    refs = [
        "J. Augé, S. Betts, G. Carofiglio, G. Grassi, M. Gysi, and J. K. d'Souza, \"Aether: Network Validation Using Agentic AI and Digital Twin,\" arXiv:2604.18233, 2026.",
        "R. Khaldi, A. Lehmann, B. Ghita, and U. Trick, \"Agentic-AI Framework for Integrated Design, Implementation, Testing, and Operation of Digital Twin Networks,\" IEEE OJ-COMS, vol. 7, pp. 4352-4375, 2026.",
        "S. Nam, N. Van Tu, and J. W.-K. Hong, \"LLM-Based AI Agent for Virtual Network Function Deployment,\" J. Network and Systems Management, vol. 34, art. 111, 2026.",
        "Y. Zhou et al., \"NetArena: Dynamic Benchmarks for AI Agents in Network Automation,\" ICLR 2026. arXiv:2506.03231.",
        "J. Liu, S. Lee, E. Losiouk, and M. Böhme, \"Evaluating LLM-Based Regression Test Generation,\" Proc. ACM Softw. Eng., vol. 3, FSE, 2026.",
        "R. Asadli, B. Hoffman, I. Protogeros, and L. Vanbever, \"Evaluating Agentic Configuration Repair for Computer Networks,\" AIWILD @ ICML 2026. arXiv:2606.06212.",
        "M. K. Hossain and W. Aljoby, \"NetIntent: Leveraging Large Language Models for End-to-End Intent-Based SDN Automation,\" IEEE OJ-COMS, 2025.",
        "A. Angi, A. Sacco, and G. Marchetto, \"LLNet: An Intent-Driven Approach to Instructing Softwarized Network Devices Using a Small Language Model,\" IEEE TNSM, vol. 22, no. 4, 2025.",
        "Y. Zhou, K. Hsieh, S. K. Mani, S. Kandula, and Z. Liu, \"MeshAgent: Enabling Reliable Network Management with Large Language Models,\" Proc. ACM POMACS (SIGMETRICS '26), 2025.",
        "R. Mondal, N. Bjørner, T. Millstein, A. Tang, and G. Varghese, \"Tackling Ambiguity in User Intent for LLM-based Network Configuration Synthesis,\" HotNets '25, 2025.",
        "S. Ding, D. Flynn, and P. Harvey, \"Automatic Generation of Digital Twins for Network Testing,\" IEEE ICDCSW, pp. 653-658, 2025.",
        "S. Knight, H. X. Nguyen, N. Falkner, R. Bowden, and M. Roughan, \"The Internet Topology Zoo,\" IEEE JSAC, vol. 29, no. 9, 2011 (NSFNET topology).",
        "A. Fogel et al., \"A General Approach to Network Configuration Analysis (Batfish),\" USENIX NSDI, 2015 (design reference for Tier 1).",
        "Q. McNemar, \"Note on the sampling error of the difference between correlated proportions,\" Psychometrika, 1947; F. Wilcoxon, \"Individual comparisons by ranking methods,\" 1945; J. L. Fleiss, 1971.",
    ]
    d.numbered(refs)


def appendix(d, R):
    from netval.agent import PLAN_SYS, INTENT_SYS
    d.h1("Appendix A - Prompts (frozen after tuning on the campus split)")
    d.code(INTENT_SYS.strip(), "Intent analyzer system prompt")
    d.code(PLAN_SYS.split("{dsl}")[0].strip(), "Test planner system prompt (DSL reference omitted, see Table 3)")
    d.h1("Appendix B - Example config excerpt and PR diff")
    c = R["A"].D.by_id["D-ACL-1-F"]
    d.code(c["diff"][:1500], "Unified diff of the faulty PR D-ACL-1-F (over-broad permit)")
    p = os.path.join(ROOT, "ci", "demo", "comment.md")
    if os.path.exists(p):
        d.code(open(p).read()[:2600], "PR comment generated by the CI gate for this PR (truncated)")
