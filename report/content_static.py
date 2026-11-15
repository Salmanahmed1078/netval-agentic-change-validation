"""Report sections that do not depend on experiment numbers (intro, implementation, dataset, setup)."""


def intro(d, R):
    d.h1("Research Topic :")
    d.p("<b>Agentic AI for network change validation in CI/CD pipelines using network digital twins</b>")
    d.p("Quick recap. In Phase 1 we argued that CI/CD pipelines for network changes are static: they either run tests that "
        "have nothing to do with a change, or they run the whole suite every time. In Phase 2 we read eight more papers, "
        "sharpened the gap (no one compares an intent-aware agent with a fixed CI suite on the <i>same</i> network changes, and "
        "nobody measures false approvals of a deployment gate) and turned the idea into a 12-step methodology with an "
        "architecture: an AI agent reads the intent of a change, works out its blast radius, writes change-specific tests, runs "
        "them in a two-tier Network Digital Twin (NDT) and returns <b>approve</b>, <b>block</b> or <b>escalate</b> to the pipeline.")
    d.p("In this phase we actually built it and measured it. Concretely, Phase 3 delivers:")
    d.bullets([
        f"A working prototype (about {R['loc']} lines of Python) of the full pipeline in Phase 2's architecture: config parser, "
        "two-tier digital twin, intent analyzer, impact analyzer, test planner, executor and a deterministic evidence judge, "
        "plus a CI/CD gate (GitHub Actions job + PR comment) and a web GUI.",
        f"A labelled dataset of <b>{R['ds']['n']} network change cases</b> over the 8 change classes and 3 topologies we "
        f"promised, each with three ticket versions (clear, vague, terse), so <b>{R['ds']['n'] * 3} change/ticket pairs</b>. "
        "Every label comes from an exhaustive oracle, not from our opinion.",
        "All four Phase-2 baselines (B1 fixed full suite, B2 rule-based, B3 one-shot LLM, B4 Batfish-style static only), "
        "evaluated on exactly the same changes and twin snapshots (paired design).",
        "Graphs, a comparison with the papers from Phases 1-2, six ablations, statistics (McNemar, Wilcoxon, bootstrap CIs, "
        "Holm correction, Fleiss' kappa) and a failure analysis with worked examples.",
    ])
    d.p("The core research question did not change: can an intent-aware agent pick and run better tests than a fixed checklist, "
        "<i>without approving changes it should not</i>? The twin is still the test bench, not the thing under study.")


def implementation(d, R):
    d.h1("1. Implementation of the Proposed Methodology")
    d.p("We implemented every block of the Phase-2 architecture diagram. Figure 1 is the architecture as it now exists in code; "
        "colours mark which parts use an LLM (blue) and which are plain deterministic code (green / orange). The LLM is used "
        "only where judgement about the <i>ticket</i> is needed; everything that can be computed exactly is computed exactly.")
    d.fig("../figures/fig_architecture.png", "Implemented architecture (same structure as the Phase-2 diagram). Blue = LLM, "
          "green = deterministic analysis on the twin, orange = rule-based evidence judge.", 100)
    d.h2("1.1 Code structure")
    rows = [
        ["netval/model.py", "Device/interface data model; renderer and parser of an IOS/FRR-like config dialect (interfaces, OSPF, BGP, "
         "static, prefix-lists, route-maps, ACLs, NAT, VLANs/trunks). Every change is a real text diff."],
        ["netval/twin.py", "The two-tier digital twin: control plane (connected/static/OSPF/eBGP/redistribution, admin distance, ECMP), "
         "data plane (ACL first-match, NAT, loops, null routes, L2 VLAN/trunk attachment) and Tier-2 emulation (DF/MTU probes, "
         "silent-failure convergence with BFD/dead timers/SPF throttle, link load from a traffic matrix)."],
        ["netval/topologies.py", "The three lab networks (campus, data-centre leaf-spine, ISP based on NSFNET from Topology Zoo)."],
        ["netval/cases.py", f"Change-case generator: {R['ds']['templates']} templates over 8 classes, safe and faulty implementations, ticket versions."],
        ["netval/oracle.py", "Exhaustive ground-truth oracle (Tier 1 + Tier 2, all pairs, all services, all links)."],
        ["netval/impact.py", "Preprocessing: structured diff, unified diff, Tier-1 semantic (differential) diff, blast radius, 2-hop subgraph."],
        ["netval/dsl.py", "Typed test language (9 test types), validator, executor on the twin, CI time-cost model, coverage keys."],
        ["netval/agent.py", "The proposed agent: intent analyzer, test planner, entry compiler, evidence judge, bounded replan loop; "
         "B3 one-shot baseline."],
        ["netval/baselines.py", "B1 fixed full suite, B2 rule-based selection, B4 Batfish-style static-only gate."],
        ["netval/llm.py", "Local (Ollama, Qwen2.5-7B-Instruct) and hosted (Claude Sonnet 5.5) back-ends with an on-disk call cache."],
        ["netval/experiment.py, analysis.py", "Paired, resumable, parallel experiment runner; metrics and statistical tests."],
        ["ci/validate_pr.py, ci/.github/...", "CI/CD gate: exit code 0/1/2 = approve/block/escalate, PR comment in Markdown."],
        ["gui/app.py", "NetVal Studio web GUI (Flask): browse cases, run the agent live, see every stage, compare with baselines."],
        ["tests/test_twin.py", "12 verification tests of the twin against known protocol behaviour."],
    ]
    d.table(["Module", "What it implements"], rows, "Main modules of the prototype", widths=[24, 76], small=True)

    d.h2("1.2 The network digital twin (two tiers)")
    d.p("Phase 2 planned Batfish for Tier 1 and containerlab + FRRouting for Tier 2. Both need Docker on Linux, which our "
        "machine does not have (Section 1.7), so we wrote the twin ourselves in Python, keeping the <i>split of responsibilities</i> "
        "that the methodology depends on:")
    d.bullets([
        "<b>Tier 1 - static analysis (Batfish-style).</b> Parses every config and computes the converged control plane: connected "
        "and static routes (next-hop resolution, null0), OSPF (adjacency compatibility checks for area, passive, subnet, "
        "hello/dead timers and MTU - an MTU mismatch leaves the adjacency stuck in ExStart, as on real routers), Dijkstra with ECMP, "
        "intra-area before external routes, eBGP sessions with route-maps and prefix-lists, redistribution between protocols, and "
        "administrative distance. The data plane is symbolic: a flow is traced hop by hop through ingress/egress ACLs (first match, "
        "implicit deny), NAT overload, VLAN/trunk attachment and every ECMP branch; it reports dispositions such as ACCEPTED, "
        "DENIED_IN, NO_ROUTE, NULL_ROUTED, LOOP and NO_RETURN. Tier 1 is exhaustive (all host pairs x all services) but has no "
        "notion of time, packet size or load.",
        "<b>Tier 2 - emulation.</b> Replays the same network with the dimensions static analysis cannot see: (a) DF probes of a "
        "given size, dropped where an interface MTU is smaller (MTU black holes); (b) a silent link failure, where detection takes "
        "the OSPF dead interval or 3 x 300 ms BFD, plus LSA flooding per hop, the SPF throttle delay and a FIB update, so we get "
        "the traffic-loss time per flow; (c) link utilisation from the telemetry traffic matrix with ECMP splitting. Tier 2 only "
        "answers for the concrete probes it is asked to send (wildcard services are sampled as icmp + tcp/443).",
    ])
    d.p("Because the twin is our own code we had to show it behaves like a network. <b>tests/test_twin.py</b> contains 12 checks "
        "against known protocol behaviour (ACL first-match and shadowing, ExStart on MTU mismatch, a consistent low MTU being "
        "invisible to Tier 1 but caught by Tier 2 DF probes, BFD vs dead-interval convergence, timer mismatch, NAT return paths, "
        "static-route loops, BGP route-map leaks, ECMP load split, oracle labelling of an overload). All 12 pass (Figure 15).")

    d.h2("1.3 The agent pipeline")
    d.p("The five components of Phase 2 Step 5 are implemented as follows. Prompts are fixed and stored in <code>netval/agent.py</code>; "
        "outputs are always JSON and are validated before use.")
    d.table(["#", "Component", "Implementation", "LLM?"], [
        ["1", "Intent analyzer", "Reads the ticket (and the diff, only to name devices) and returns goal, scope, expected effects, "
         "things that must not change and a <b>confidence</b> judged from the ticket text alone (high / medium / low).", "Yes"],
        ["2", "Impact analyzer", "Runs the Tier-1 differential between the pre- and post-change snapshots and lists every behavioural "
         "difference as an <i>impact entry</i> with an id: changed flows (per service), routes added/removed, adjacencies and BGP "
         "sessions, changed traffic paths, links receiving shifted demand, and Tier-2-relevant config objects (MTU, timer, BFD, SPF "
         "changes).", "No"],
        ["3", "Test planner", "Labels every impact entry <i>intended</i>, <i>unintended</i> or <i>unsure</i> against the ticket, and adds "
         "extra tests in the typed DSL for effects the ticket promises (e.g. a 9000-byte probe, a path via X). Labelled entries are "
         "compiled deterministically into tests: intended -> expect the new behaviour, unintended -> expect 'unchanged'. Load / MTU / "
         "timer entries always become Tier-2 safety tests.", "Yes"],
        ["4", "Test executor", "Validates every test (unknown names, wrong fields -> 'invalid'), picks the cheapest tier that can run it "
         "and executes it on the twin. Records evidence and a modelled CI time per test.", "No"],
        ["5", "Evidence judge + gate", "Plain code, never the LLM (rules in Table 4). Missing evidence sends the planner back with "
         "feedback, at most 3 replans.", "No"],
    ], "Agent components as implemented", widths=[4, 17, 70, 9], small=True)
    d.h3("Typed test language")
    d.p("Following the LLNet lesson from Phase 2 (structured outputs beat free code), the LLM never writes scripts. It fills "
        "objects of a small typed language, which the executor validates and runs:")
    d.table(["Test", "Fields", "Tier", "Meaning"], [
        ["reach", "src, dst, svc|*, expect allow/deny/unchanged", "1 or 2", "flow disposition (incl. stateful return path)"],
        ["path", "src, dst, via, expect true/false", "1 or 2", "all ECMP paths go through a router"],
        ["route", "dev, prefix, expect present/absent/unchanged", "1 or 2", "route in a device's RIB"],
        ["acl", "dev, acl, src, dst, svc, expect", "1", "formal ACL decision"],
        ["adj", "a, b, expect up/down/unchanged", "1 or 2", "OSPF adjacency on a link"],
        ["like", "host, as", "1 or 2", "after a VLAN move the host has exactly the access of a reference host"],
        ["mtu", "src, dst, size, expect pass/fail", "2", "DF probe of a given size"],
        ["conv", "a, b, max_s", "2", "traffic-loss time after a silent failure of a link"],
        ["load", "a, b, max_pct", "2", "utilisation of a link under the telemetry traffic matrix"],
        ["inv", "no_loops / mgmt_reachable / isolation / no_leak", "1", "network invariants (appended automatically)"],
    ], "The typed test DSL", widths=[9, 38, 9, 44], small=True)
    d.h3("Evidence judge rules")
    d.table(["Order", "Rule", "Verdict"], [
        ["1", "Any executed test or invariant fails", "BLOCK"],
        ["2", "The planner marked any impact entry 'unsure' (ambiguous ticket)", "ESCALATE"],
        ["3", "Intent confidence is low and the change alters behaviour", "ESCALATE"],
        ["4", "Invalid tests, impacted objects without a test, or nothing confirms the change does what it should", "REPLAN (max 3), then ESCALATE"],
        ["5", "Otherwise: all tests pass, 100% of impacted objects covered, no tool error", "APPROVE"],
    ], "Deterministic evidence-judge rules (checked in this order)", widths=[9, 70, 21], small=True)

    d.h2("1.4 CI/CD integration")
    d.p("<code>ci/validate_pr.py</code> takes the base branch, the PR branch and the PR description, runs the agent and writes a "
        "verdict JSON and a Markdown PR comment (verdict, reason, every test with tier and evidence, the agent trace). The exit code "
        "is 0 = approve, 1 = block (fails the pipeline), 2 = escalate (request a human reviewer). "
        "<code>ci/.github/workflows/netval.yml</code> is the GitHub Actions job for the network source-of-truth repository; it is "
        "meant for a self-hosted runner with the local model, so configs never leave the company. Section 8 shows the gate "
        "blocking a faulty PR and approving the correct one.")

    d.h2("1.5 Changes to the Phase-2 methodology (and feedback)")
    d.p("We did not receive specific written feedback on Phase 2 in class, so this list documents every place where the "
        "implementation differs from what we promised, and why. We think it is more honest to state them than to hide them.")
    d.table(["Phase-2 plan", "Phase-3 implementation", "Reason / effect"], [
        ["Tier 1 = Batfish, Tier 2 = containerlab + FRR", "Own Python twin with the same two tiers (Section 1.2), verified by 12 tests",
         "No Docker/Linux on our machine (Sec. 1.7). Keeps the static-vs-emulation split; protocol fidelity is lower than real FRR."],
        ["Twin time = real tool time", "Modelled CI time per test (Table 11), Batfish/containerlab-like costs + measured LLM latency",
         "Our twin answers in milliseconds, which would make every time comparison meaningless."],
        ["~120 change cases, half unsafe", f"{R['ds']['n']} cases, {R['ds']['unsafe']} unsafe / {R['ds']['safe']} safe, from {R['ds']['templates']} templates",
         "As planned. Some templates are fault-only (the ticket itself is harmful, e.g. a drain that overloads the backup path)."],
        ["Labels checked by both of us + a third reviewer", "Labels from an exhaustive oracle; generator intent cross-checked; variants "
         "the oracle disagreed with were dropped", "No time for manual dual-labelling of 360 pairs; the oracle is reproducible."],
        ["Split 20% / 80% by topology", "Campus (33 cases, 27.5%) for prompt tuning, data-centre + ISP (87 cases) for testing",
         "Kept the 'tune on one topology, test on unseen ones' rule; campus is smaller than planned."],
        ["Main model hosted, plus local Qwen2.5-7B", "Hosted Claude Sonnet 5.5 for all experiments; local Qwen2.5-7B on a stratified "
         f"subset of {R.get('local_n', 32)} test cases (clear tickets)", "Local inference ran at 2-4 tokens/s on a 16 GB laptop under "
         "memory pressure; the full grid would have taken days."],
        ["5 repeats per LLM run", "3 repeats for the main agent (all ticket versions); 1 for baselines and ablations",
         "Cost/time; agreement across repeats is high (Section 7), so more repeats add little."],
        ["Temperature 0.2", "Local model 0.2; hosted Sonnet 5.5 uses its default (the API rejects other values)", "API constraint."],
        ["Impact analyzer as an agent", "Deterministic Tier-1 differential", "It is computable exactly; using an LLM would only add errors."],
        ["Planner writes the whole plan", "Planner labels impact entries + writes extra tests; labelled entries compiled to tests",
         "Fewer output tokens and no hallucinated object names for the parts that are mechanical."],
        ["(not in Phase 2)", "New DSL test 'like' for VLAN moves; host MTU in the context package",
         "Found during prompt tuning on the campus split: the planner guessed access rules for moved hosts."],
    ], "Change log: Phase-2 plan vs Phase-3 implementation", widths=[25, 38, 37], small=True)

    d.h2("1.6 Implementation details and reproducibility")
    d.table(["Setting", "Value"], [
        ["Hosted LLM", "claude-sonnet-5-5 (Anthropic API), effort 'medium', thinking only between tool calls, server-side refusal fallback on"],
        ["Local LLM", "qwen2.5:7b-instruct (Q4_K_M) via Ollama 0.40, temperature 0.2, seed = repeat id, JSON mode, context 6144, 2 parallel slots"],
        ["Replan budget / coverage threshold", "max 3 replans; 100% of impacted objects (path-only entries excluded)"],
        ["Topology context", "2-hop subgraph around the changed devices (k = 2)"],
        ["SLAs (invariants)", "failover <= 3 s, link utilisation <= 90%; no loops; management reachability; isolation pairs; no leak of internal prefixes"],
        ["Repeats", "agent (hosted): 3 x 3 ticket versions; baselines/ablations: 1; deterministic baselines are run once"],
        ["Caching / logs", "every LLM call cached on disk by (model, prompt, seed); every run logged as JSONL with trace, tests and evidence"],
        ["Run order", "jobs shuffled implicitly by thread scheduling; results keyed by (case, ticket, repeat), so runs are resumable"],
    ], "Experimental configuration", widths=[28, 72], small=True)
    d.p("Everything is reproduced with: <code>python gen_dataset.py</code> (dataset), <code>./run_hosted.sh</code> and "
        "<code>./run_local.sh</code> (experiments), <code>python analyze.py</code> (tables/figures) and "
        "<code>python report/build.py</code> (this report). With the LLM cache present, the whole evaluation replays offline.")

    d.h2("1.7 Hardware / software compatibility")
    d.table(["Item", "Our device / version", "Compatibility note"], [
        ["Machine", "Apple MacBook, M4 (10 cores), 16 GB unified memory, macOS 26.5", "Phase 2 assumed a 16-core / 32 GB Linux box + GPU"],
        ["Container runtime", "not available (no Docker Desktop)", "Batfish and containerlab need Docker; containerlab is Linux-first, "
         "so Tier 1/2 were re-implemented in Python"],
        ["Python", "3.12.15 (venv): numpy 2.5, scipy 1.18, networkx 3.7, statsmodels 0.15, matplotlib 3.11, flask 3.1, anthropic 2.8", "all pure-Python, cross-platform"],
        ["Local LLM runtime", "Ollama 0.40.16, Qwen2.5-7B-Instruct Q4 (4.7 GB), Metal GPU", "fits in 16 GB, but slowed to 2-4 tok/s when the "
         "system was swapping (browser using several GB)"],
        ["Hosted LLM", "Claude Sonnet 5.5 via API key in a local .env file (never committed)", f"total experiment cost about ${R.get('api_cost', 0):.0f}"],
        ["Report / screenshots", "Google Chrome 157 (headless) for PDF and GUI screenshots", "-"],
    ], "Hardware and software used", widths=[18, 45, 37], small=True)


def dataset(d, R):
    ds = R["ds"]
    d.h1("2. Dataset and Preprocessing")
    d.h2("2.1 Topologies")
    d.p("We built the three networks we promised (Figure 2). All configs are text files in our IOS/FRR-like dialect, so a change is "
        "a real pull request diff. Each snapshot also has a host inventory, the physical links, the operator's intent policy "
        "(invariants and SLAs) and a telemetry traffic matrix used by Tier 2.")
    d.fig("../figures/fig_topologies.png", "The three lab topologies. Dark blue = routers, teal = L2 switches, purple = external "
          "ASes (internet, WAN, customers, peers), white = hosts. Line width is proportional to link capacity.", 100)
    d.table(["Topology", "Devices", "Hosts", "Links", "Protocols / features", "Use"], [
        ["Campus", "10", "14", "13", "OSPF, eBGP to ISP, NAT overload, 4 user VLANs on 3 access switches, server/NOC segments, 5 ACLs", "prompt tuning"],
        ["Data centre (leaf-spine)", "12", "11", "20", "2 spines, 6 leaves (L3 gateways), 2 borders with eBGP to a WAN, 2 tenants, jumbo-capable servers", "test"],
        ["ISP (NSFNET + edges)", "20", "9", "28", "14-router NSFNET backbone (Topology Zoo), 3 customers (one dual-homed), 2 upstream peers, NOC VLANs", "test"],
    ], "Topologies", widths=[18, 9, 8, 8, 45, 12], small=True)
    d.h2("2.2 Change cases and fault families")
    d.p(f"The generator (<code>netval/cases.py</code>) contains {ds['templates']} change templates. Each template is one realistic change request. "
        f"For most templates we generate the <b>correct implementation</b> and one <b>faulty implementation of the same ticket</b>, "
        f"with the fault drawn from the families used by NetArena [4] and CORNETTO [6] (wrong port, shadowed rule, over-broad rule, "
        f"wrong ACL, unresolvable next hop, static route on the wrong device creating a loop, one-sided OSPF cost, cost typo, "
        f"timer mismatch, BFD removed, SPF delay typo, MTU on one end only, MTU value typo, prefix-list typo, route-map leak, "
        f"redistribution without route-map, wrong VLAN id, host not re-addressed, interface shut on the wrong port, and more). "
        f"Some templates are <b>fault-only</b>: the diff does exactly what the ticket says, but the ticket itself is harmful "
        f"(draining a link that overloads the backup path, a static pin that creates a micro-loop on failure). "
        f"The result is <b>{ds['n']} cases ({ds['unsafe']} unsafe, {ds['safe']} safe)</b> covering all 8 classes and "
        f"{ds['families']} distinct fault families (Figure 3).")
    d.fig("../figures/fig_dataset.png", "Dataset composition: safe/unsafe cases per change class (left) and the most frequent "
          "fault families among the unsafe cases (right).", 100)
    d.h2("2.3 Three ticket versions per case")
    d.p("Following Cleverest [5], every case has three tickets: <b>clear</b> (devices, objects and expected effect), <b>vague</b> "
        "(goal only) and <b>terse</b> (only an ID). The faulty and the correct implementation of a template share the same tickets, "
        "so the only difference the agent can use is the network behaviour. Example (template D-ACL-1):")
    d.table(["Version", "Ticket text"], [[k, v] for k, v in ds["example_tickets"].items()],
            "The three ticket versions of one change", widths=[12, 88], small=True)
    d.h2("2.4 Ground truth: the exhaustive oracle")
    d.p("Phase 2 asked for ground truth from an exhaustive oracle. <code>netval/oracle.py</code> profiles the network before and "
        "after the change with <b>both tiers, exhaustively</b>: every host pair x 7 services, every route on every device, every "
        "adjacency, all four invariants, 1500-byte and jumbo DF probes between all host pairs, the failover time of every redundant "
        "link and the utilisation of every link. Each template also carries a machine-readable intent spec (which flows, routes, "
        "adjacencies, paths, probes or drained links the ticket asks for). A change is <b>unsafe</b> if an invariant breaks, the "
        "intended effect is not achieved, or <i>anything else</i> changed that the intent does not explain (reachability, routes, "
        "adjacencies, MTU, convergence SLA, utilisation SLA). A generated 'safe' variant that the oracle flags, or a 'fault' "
        "it does not flag, is discarded, so labels and variants never disagree.")
    d.h2("2.5 Preprocessing and feature engineering")
    d.bullets([
        "<b>Parsing / normalisation</b>: every config is parsed into the vendor-neutral model; the round trip parse(render(x)) = x "
        "is checked for every device (test 1).",
        "<b>Two diffs</b>: a unified Git diff (what B3 and humans see) and a structured diff by device and config section "
        "(e.g. <code>leaf3 | ip access-list ACL_DBA_OUT | + 25 permit ...</code>).",
        "<b>Semantic diff</b>: the Tier-1 differential (like Batfish's differential reachability) turned into numbered impact entries.",
        "<b>Context package</b> (JSON-like text): ticket, intent analysis, structured diff, 2-hop subgraph (devices, hosts, links "
        "with capacity), host list with address/group/MTU, invariants and SLAs, impact entries and the DSL reference.",
        "<b>Secrets</b>: the generated configs contain none; the API key is read from <code>.env</code> and never logged.",
        "<b>Split</b>: campus = tuning split (33 cases, 99 ticket pairs) used only for prompt design; data centre + ISP = test "
        "split (87 cases, 261 ticket pairs). All numbers in Sections 3-7 are on the test split.",
    ])
