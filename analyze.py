"""Produce every table and figure of the Phase-3 report from data/results/*.jsonl.

    .venv/bin/python analyze.py
"""
import json
import os
from collections import Counter, defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np

from netval.analysis import rates, bootstrap_ci, paired, mcnemar, wilcoxon, holm, fleiss_kappa, unanimity, collapse
from netval.experiment import Data, load
from netval.cases import CLASS_LABEL, CLASSES
from netval.topologies import NSF

os.makedirs("figures", exist_ok=True)
os.makedirs("results", exist_ok=True)
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150,
                     "axes.titleweight": "bold", "axes.titlesize": 11})
C = {"agent": "#1f4e8c", "B1": "#98a2b3", "B2": "#f79009", "B3": "#7a5af8", "B4": "#0e9384", "hosted": "#d92d20"}
NAMES = {"B1_full_suite": "B1 Fixed full suite", "B2_rule_based": "B2 Rule-based", "B3_oneshot_local": "B3 One-shot LLM (local)",
         "B4_batfish_only": "B4 Batfish-only", "agent_local": "Proposed agent (local Qwen-7B)",
         "agent_hosted": "Proposed agent (hosted Sonnet 5.5)", "B3_oneshot_hosted": "B3 One-shot LLM (hosted)"}
SHORT = {"B1_full_suite": "B1 Full", "B2_rule_based": "B2 Rules", "B3_oneshot_local": "B3 One-shot (local)",
         "B4_batfish_only": "B4 Batfish", "agent_local": "Agent (local)", "agent_hosted": "Agent (hosted)",
         "B3_oneshot_hosted": "B3 One-shot"}
COL = {"B1_full_suite": C["B1"], "B2_rule_based": C["B2"], "B3_oneshot_local": C["B3"], "B4_batfish_only": C["B4"],
       "agent_local": "#e04f39", "agent_hosted": C["agent"], "B3_oneshot_hosted": C["B3"], "B3_oneshot_local": "#b692f6"}
D = Data()
TEST_IDS = {c["id"] for c in D.split("test")}
OUT = {}


def get(method, intents=("clear", "vague", "terse"), test_only=True, mode="first"):
    rs = load(method)
    if not rs:
        return []
    if method in ("B1_full_suite", "B2_rule_based", "B4_batfish_only"):   # intent-independent: replicate
        rs = [dict(r, intent=i) for r in rs for i in ("clear", "vague", "terse")]
    rs = [r for r in rs if r["intent"] in intents and (not test_only or r["case"] in TEST_IDS)]
    return collapse(rs, mode)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(f"figures/{name}.png", bbox_inches="tight")
    fig.savefig(f"figures/{name}.pdf", bbox_inches="tight")
    plt.close(fig)


# =========================================================================== dataset figures
def fig_dataset():
    cs = D.cases
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.6), gridspec_kw={"width_ratios": [1.5, 1]})
    s = [sum(c["cls"] == k and c["label"] == "safe" for c in cs) for k in CLASSES]
    u = [sum(c["cls"] == k and c["label"] == "unsafe" for c in cs) for k in CLASSES]
    x = np.arange(len(CLASSES))
    ax[0].bar(x, s, color="#12b76a", label="safe")
    ax[0].bar(x, u, bottom=s, color="#f04438", label="unsafe")
    ax[0].set_xticks(x, [CLASS_LABEL[k].split(" (")[0].replace(" / ", "/\n").replace(" ", "\n", 1) for k in CLASSES], fontsize=8)
    ax[0].set_ylabel("change cases")
    ax[0].set_title("Change cases per class (oracle label)")
    ax[0].legend(frameon=False)
    f = Counter(c["variant"] for c in cs if c["label"] == "unsafe")
    top = f.most_common(14)
    ax[1].barh([k.replace("_", " ") for k, _ in top][::-1], [v for _, v in top][::-1], color="#f04438")
    ax[1].set_title("Fault families in unsafe cases (top 14)")
    ax[1].tick_params(axis="y", labelsize=7.5)
    save(fig, "fig_dataset")


def fig_topologies():
    from gui.app import POS
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
    for ax, topo in zip(axs, ["campus", "datacenter", "isp"]):
        s = D.bases[topo]
        G = nx.Graph()
        pos = {k: (v[0], -v[1]) for k, v in POS[topo].items()}
        for a, ia, b, ib in s.links:
            G.add_edge(a, b, bw=s.devices[a].interfaces[ia].bandwidth)
        for h in s.hosts.values():
            d = h.attach[0]
            G.add_edge(d, h.name, bw=0)
            sib = [x for x in G.neighbors(d) if x in s.hosts]
            k = sib.index(h.name)
            up = d in s.policy["invariants"]["no_leak"]["externals"] and topo == "campus"
            off = (k - (len(sib) - 1) / 2) * 52
            pos[h.name] = (pos[d][0] + off + (90 if up else 0), pos[d][1] + (0 if up else -62 - 22 * (k % 2)))
            fixed = {"c1-host": (150, 15), "ext-dns1": (850, 20), "ext-dns2": (40, 360), "c3-host": (885, 345),
                     "c2-host": (700, 440)}
            if topo == "isp" and h.name in fixed:
                pos[h.name] = (fixed[h.name][0], -fixed[h.name][1])
        widths = [0.6 + min(3.5, G[a][b]["bw"] / 25000) if G[a][b]["bw"] else 0.5 for a, b in G.edges]
        ext = set(s.policy["invariants"]["no_leak"]["externals"])
        col = ["#7a5af8" if n in ext else "#0e9384" if n in s.devices and s.devices[n].kind == "switch" else
               "#1f4e8c" if n in s.devices else "#f2f4f7" for n in G.nodes]
        size = [700 if n in s.devices else 300 for n in G.nodes]
        nx.draw_networkx_edges(G, pos, ax=ax, width=widths, edge_color="#98a2b3")
        nx.draw_networkx_nodes(G, pos, ax=ax, node_color=col, node_size=size, edgecolors="#475467", linewidths=0.6)
        nx.draw_networkx_labels(G, pos, ax=ax, labels={n: n for n in G.nodes if n in s.devices}, font_size=5.6, font_color="white", font_weight="bold")
        nx.draw_networkx_labels(G, pos, ax=ax, labels={n: n for n in G.nodes if n in s.hosts}, font_size=4.8, font_color="#344054")
        ax.set_title(f"{topo}: {len(s.devices)} devices, {len(s.hosts)} hosts, {len(s.links)} links")
        ax.axis("off")
    save(fig, "fig_topologies")


def fig_architecture():
    fig, ax = plt.subplots(figsize=(12, 6.2))
    ax.axis("off")
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 6.6)

    def box(x, y, w, h, t, fc, ec="#1f4e8c", fs=9, bold=True, tc="#101828"):
        ax.add_patch(matplotlib.patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.12", fc=fc, ec=ec, lw=1.2))
        ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=fs, fontweight="bold" if bold else "normal", color=tc, wrap=True)

    def arr(x1, y1, x2, y2, st="-|>", ls="-", c="#475467"):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle=st, color=c, lw=1.3, ls=ls))
    box(0.3, 5.7, 3.4, 0.7, "Change ticket (clear / vague / terse)", "#eef4ff")
    box(4.3, 5.7, 3.4, 0.7, "Config diff = Git pull request\n(configs/*.cfg, inventory/hosts.json)", "#eef4ff", fs=8)
    box(8.3, 5.7, 3.4, 0.7, "Pre-change snapshot\n(topology, configs, policy, traffic)", "#eef4ff", fs=8)
    box(0.3, 4.65, 11.4, 0.6, "Preprocessing: parse configs (netval.model) · structured diff · Tier-1 semantic diff · 2-hop subgraph · invariants",
        "#f9fafb", ec="#98a2b3", fs=8.5)
    for x in (2, 6, 10):
        arr(x, 5.7, x, 5.25)
    ax.add_patch(matplotlib.patches.FancyBboxPatch((0.3, 1.95), 8.2, 2.35, boxstyle="round,pad=0.04", fc="#f5f8ff", ec="#2f6fd0", lw=1.4, ls="--"))
    ax.text(0.45, 4.12, "Agentic validator (bounded loop, max 3 replans)", fontsize=8.5, color="#2f6fd0", fontweight="bold")
    box(0.5, 3.0, 1.85, 0.9, "1 Intent analyzer\nLLM", "#dbe8ff", fs=8.5)
    box(2.55, 3.0, 1.85, 0.9, "2 Impact analyzer\ndeterministic", "#e6f4ec", ec="#1a7f4b", fs=8.5)
    box(4.6, 3.0, 1.85, 0.9, "3 Test planner\nLLM labels + typed DSL", "#dbe8ff", fs=8)
    box(6.6, 3.0, 1.75, 0.9, "4 Executor\nruns DSL on twin", "#e6f4ec", ec="#1a7f4b", fs=8.5)
    box(2.0, 2.1, 6.0, 0.65, "5 Evidence judge (plain code, not the LLM)\nany failed test/invariant -> BLOCK · unsure / low confidence / missing evidence -> ESCALATE · else APPROVE",
        "#fff4e0", ec="#a15c00", fs=7.2)
    for x1, x2 in ((2.35, 2.55), (4.4, 4.6), (6.45, 6.6)):
        arr(x1, 3.45, x2, 3.45)
    arr(7.45, 3.0, 6.5, 2.75)
    arr(3.4, 2.75, 5.5, 3.0, ls="--", c="#d92d20")
    ax.text(3.3, 2.82, "missing evidence -> replan", fontsize=7, color="#d92d20")
    box(9.0, 3.3, 2.7, 0.95, "Tier 1 static (Batfish-style)\nRIB/FIB, ACL, NAT, BGP,\nall-pairs reachability", "#e6f4ec", ec="#1a7f4b", fs=7.5, bold=False)
    box(9.0, 2.1, 2.7, 0.95, "Tier 2 emulation\nDF/MTU probes, OSPF timers,\nBFD/SPF failover, link load", "#e6f4ec", ec="#1a7f4b", fs=7.5, bold=False)
    arr(8.35, 3.45, 9.0, 3.7)
    arr(8.35, 3.3, 9.0, 2.6)
    ax.text(10.35, 4.38, "Network digital twin", ha="center", fontsize=8.5, fontweight="bold", color="#1a7f4b")
    for i, (t, c) in enumerate((("APPROVE", "#12b76a"), ("BLOCK", "#f04438"), ("ESCALATE", "#f79009"))):
        box(1.0 + i * 2.3, 1.15, 1.9, 0.5, t, c, ec=c, fs=9, tc="white")
        arr(5.0, 2.1, 1.95 + i * 2.3, 1.65)
    box(8.0, 1.15, 3.7, 0.5, "CI/CD: verdict + evidence posted as PR comment", "#eef4ff", fs=8)
    box(0.3, 0.1, 11.4, 0.7, "Evaluation layer: same 120 cases × 3 tickets · B1 full suite · B2 rule-based · B3 one-shot LLM · B4 Batfish-only · "
        "oracle labels · McNemar/Wilcoxon/bootstrap · ablations · failure taxonomy", "#f2f4f7", ec="#98a2b3", fs=8, bold=False)
    save(fig, "fig_architecture")


# =========================================================================== main results
MAIN = ["B1_full_suite", "B2_rule_based", "B4_batfish_only", "B3_oneshot_hosted", "agent_hosted", "agent_local"]


def main_table():
    rows, lines = [], []
    for m in MAIN + ["B3_oneshot_local"]:
        for it in ("clear", "vague", "terse", "all"):
            rs = get(m, intents=("clear", "vague", "terse") if it == "all" else (it,))
            if not rs:
                continue
            r = rates(rs)
            r.update(method=m, intent=it)
            for k in ("detection", "false_approval", "false_block", "escalation_rate", "correct"):
                r[k + "_ci"] = bootstrap_ci(rs, k, B=800)
            rows.append(r)
    OUT["main"] = rows
    json.dump(rows, open("results/main_table.json", "w"), indent=1)
    # html summary for GUI
    h = "<table><tr><th>Method</th><th>Detection</th><th>False approval</th><th>False block</th><th>Escalation</th><th>Tests</th><th>CI time (s)</th></tr>"
    for r in rows:
        if r["intent"] == "clear":
            h += (f"<tr><td>{NAMES.get(r['method'], r['method'])}</td><td>{r['detection']:.0%}</td><td>{r['false_approval']:.0%}</td>"
                  f"<td>{r['false_block']:.0%}</td><td>{r['escalation_rate']:.0%}</td><td>{r['tests']:.0f}</td><td>{r['ci_seconds']:.0f}</td></tr>")
    open("results/summary_table.html", "w").write(h + "</table>")
    return rows


def fig_main_rates(rows):
    ms = [m for m in MAIN if any(r["method"] == m and r["intent"] == "clear" for r in rows)]
    keys = [("detection", "Fault detection"), ("false_approval", "False approval"), ("false_block", "False block"),
            ("escalation_rate", "Escalation rate")]
    fig, ax = plt.subplots(figsize=(11, 3.9))
    w = 0.8 / len(ms)
    for i, m in enumerate(ms):
        r = next(r for r in rows if r["method"] == m and r["intent"] == "clear")
        vals = [r[k] for k, _ in keys]
        err = np.array([[r[k] - r[k + "_ci"][0], r[k + "_ci"][1] - r[k]] for k, _ in keys]).T
        xs = np.arange(len(keys)) + i * w - 0.4 + w / 2
        ax.bar(xs, vals, w, color=COL[m], label=SHORT[m], yerr=err, capsize=2, error_kw={"lw": 0.8})
        for x, v in zip(xs, vals):
            ax.text(x, v + 0.02, f"{v:.0%}", ha="center", fontsize=6.5)
    ax.set_xticks(range(len(keys)), [k[1] for k in keys])
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("rate (clear tickets, test split)")
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(ncol=6, frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, 1.13))
    save(fig, "fig_main_rates")


def fig_time_vs_detection(rows):
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8))
    for m in MAIN:
        r = next((r for r in rows if r["method"] == m and r["intent"] == "clear"), None)
        if not r:
            continue
        ax[0].scatter(r["ci_seconds"], r["detection"], s=90, color=COL[m], edgecolor="k", zorder=3)
        ax[0].annotate(SHORT[m], (r["ci_seconds"], r["detection"]), textcoords="offset points", xytext=(6, -10), fontsize=8)
        ax[1].scatter(r["tests"], 1 - r["false_approval"], s=90, color=COL[m], edgecolor="k", zorder=3)
        ax[1].annotate(SHORT[m], (r["tests"], 1 - r["false_approval"]), textcoords="offset points", xytext=(6, -10), fontsize=8)
    ax[0].set_xscale("log")
    ax[0].set_xlabel("mean modelled CI time per change (s, log)")
    ax[0].set_ylabel("fault detection rate")
    ax[0].set_title("Detection vs validation time")
    ax[1].set_xscale("log")
    ax[1].set_xlabel("mean tests executed per change (log)")
    ax[1].set_ylabel("1 - false approval rate")
    ax[1].set_title("Safety vs number of tests")
    for a in ax:
        a.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        a.grid(alpha=.3)
    save(fig, "fig_time_vs_detection")


def fig_tests_time_box():
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.6))
    data_t, data_s, labels = [], [], []
    for m in MAIN:
        rs = get(m, intents=("clear",))
        if not rs:
            continue
        data_t.append([r.get("n_tests_total", r["n_tests"]) for r in rs])
        data_s.append([r["ci_seconds"] / 60 for r in rs])
        labels.append(SHORT[m])
    for a, dd, t in ((ax[0], data_t, "tests executed per change"), (ax[1], data_s, "modelled CI time per change (min)")):
        bp = a.boxplot(dd, patch_artist=True, showfliers=False, widths=.55)
        for p, m in zip(bp["boxes"], [m for m in MAIN if get(m, intents=("clear",))]):
            p.set_facecolor(COL[m])
            p.set_alpha(.75)
        a.set_yscale("log")
        a.set_xticks(range(1, len(labels) + 1), labels, rotation=20, fontsize=8)
        a.set_title(t)
        a.grid(alpha=.3, axis="y")
    save(fig, "fig_tests_time_box")


def fig_intent_quality(rows):
    ms = [m for m in ("agent_hosted", "agent_local", "B3_oneshot_hosted", "B3_oneshot_local") if any(r["method"] == m for r in rows)]
    keys = [("detection", "Fault detection"), ("false_approval", "False approval"), ("escalation_rate", "Escalation rate"),
            ("safe_approval", "Safe changes auto-approved")]
    fig, axs = plt.subplots(1, 4, figsize=(13, 3.3))
    for ax, (k, t) in zip(axs, keys):
        for m in ms:
            ys = [next((r[k] for r in rows if r["method"] == m and r["intent"] == it), np.nan) for it in ("clear", "vague", "terse")]
            ax.plot(["clear", "vague", "terse"], ys, "o-", color=COL[m], label=SHORT[m], lw=2)
        ax.set_title(t)
        ax.set_ylim(-0.03, 1.03)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        ax.grid(alpha=.3)
    axs[0].legend(frameon=False, fontsize=8)
    save(fig, "fig_intent_quality")


def fig_per_class():
    ms = [m for m in MAIN if get(m, intents=("clear",))]
    M = np.full((len(ms), len(CLASSES)), np.nan)
    A = np.full((len(ms), len(CLASSES)), np.nan)
    for i, m in enumerate(ms):
        rs = get(m, intents=("clear",))
        for j, k in enumerate(CLASSES):
            sub = [r for r in rs if r["cls"] == k]
            if sub:
                r = rates(sub)
                M[i, j] = r["correct"]
                A[i, j] = r["false_approval"]
    fig, ax = plt.subplots(1, 2, figsize=(13, 3.6))
    for a, X, t, cmap in ((ax[0], M, "Correct verdict rate (block unsafe / approve safe)", "Blues"),
                          (ax[1], A, "False approval rate (unsafe changes approved)", "Reds")):
        im = a.imshow(X, cmap=cmap, vmin=0, vmax=1, aspect="auto")
        a.set_yticks(range(len(ms)), [SHORT[m] for m in ms], fontsize=8)
        a.set_xticks(range(len(CLASSES)), [CLASS_LABEL[k].split(" (")[0].split(" /")[0] for k in CLASSES], rotation=30, ha="right", fontsize=8)
        for i in range(len(ms)):
            for j in range(len(CLASSES)):
                if not np.isnan(X[i, j]):
                    a.text(j, i, f"{X[i,j]:.0%}", ha="center", va="center", fontsize=7, color="white" if X[i, j] > .6 else "black")
        a.set_title(t)
    save(fig, "fig_per_class")


def fig_confusion():
    ms = [m for m in ("agent_hosted", "agent_local", "B1_full_suite", "B4_batfish_only") if get(m, intents=("clear",))]
    fig, axs = plt.subplots(1, len(ms), figsize=(3.3 * len(ms), 2.9))
    axs = np.atleast_1d(axs)
    for ax, m in zip(axs, ms):
        rs = get(m, intents=("clear",))
        X = np.array([[sum(r["label"] == l and r["verdict"] == v for r in rs) for v in ("approve", "block", "escalate")] for l in ("safe", "unsafe")])
        ax.imshow(X, cmap="Blues")
        for i in range(2):
            for j in range(3):
                ax.text(j, i, X[i, j], ha="center", va="center", fontsize=11, color="white" if X[i, j] > X.max() * .6 else "black")
        ax.set_xticks(range(3), ["approve", "block", "escalate"])
        ax.set_yticks(range(2), ["safe", "unsafe"])
        ax.set_title(SHORT[m], fontsize=9)
    save(fig, "fig_confusion")


# =========================================================================== statistics
def stats_tables():
    agent = get("agent_hosted")
    res = {}
    pv = {}
    for b in ("B1_full_suite", "B2_rule_based", "B4_batfish_only", "B3_oneshot_hosted", "agent_local"):
        other = get(b)
        if not agent or not other:
            continue
        P = paired(agent, other)
        unsafe = [p for p in P if p[0]["label"] == "unsafe"]
        safe = [p for p in P if p[0]["label"] == "safe"]
        det = mcnemar(unsafe, lambda r: r["verdict"] in ("block", "escalate"))
        fa = mcnemar(unsafe, lambda r: r["verdict"] == "approve")
        fb = mcnemar(safe, lambda r: r["verdict"] == "block")
        wt = wilcoxon(P, "ci_seconds")
        for r in P:
            for x in r:
                x["_tests"] = x.get("n_tests_total", x["n_tests"])
        wn = wilcoxon(P, "_tests")
        wc = wilcoxon(P, "required_coverage")
        res[b] = {"n_pairs": len(P), "detection": det, "false_approval": fa, "false_block": fb, "time": wt, "tests": wn, "coverage": wc}
        for k in ("detection", "false_approval", "false_block", "time", "tests", "coverage"):
            pv[f"{b}:{k}"] = res[b][k]["p"]
    adj = holm(pv)
    for k, p in adj.items():
        b, met = k.split(":")
        res[b][met]["p_holm"] = p
    OUT["stats"] = res
    json.dump(res, open("results/stats.json", "w"), indent=1)
    # consistency
    cons = {}
    for m in ("agent_hosted", "agent_local", "B3_oneshot_hosted"):
        rs = [r for r in load(m) if r["case"] in TEST_IDS]
        if len({r["repeat"] for r in rs}) > 1:
            cons[m] = {"fleiss_kappa": fleiss_kappa(rs), "unanimous": unanimity(rs), "repeats": sorted({r["repeat"] for r in rs})}
            maj = rates(collapse(rs, "majority"))
            cons[m]["majority_vote"] = {k: maj[k] for k in ("detection", "false_approval", "false_block", "escalation_rate")}
    OUT["consistency"] = cons
    json.dump(cons, open("results/consistency.json", "w"), indent=1)


# =========================================================================== ablations
ABL = [("agent_hosted", "Full agent"), ("abl_no_impact_hosted", "- impact analyzer"), ("abl_no_feedback_hosted", "- feedback loop"),
       ("abl_no_invariants_hosted", "- invariant guard"), ("abl_tier1_only_hosted", "Tier 1 only (static)"),
       ("abl_tier2_only_hosted", "Tier 2 only (emulation)")]


def ablations():
    rows = []
    for m, lab in ABL:
        rs = get(m, intents=("clear",))
        if not rs:
            continue
        r = rates(rs)
        allrs = get(m)
        r.update(method=m, label=lab, runs_with_replans=sum(x["replans"] > 0 for x in allrs), n_all=len(allrs))
        rows.append(r)
    # intent ablation (from the main agent) and model ablation
    for it in ("vague", "terse"):
        rs = get("agent_hosted", intents=(it,))
        if rs:
            r = rates(rs)
            r.update(method=f"agent_hosted:{it}", label=f"{it} ticket (not clear)")
            rows.append(r)
    rs = get("agent_local", intents=("clear",))
    if rs:
        r = rates(rs)
        r.update(method="agent_local", label="local 7B model (32-case subset)")
        rows.append(r)
    OUT["ablation"] = rows
    json.dump(rows, open("results/ablation.json", "w"), indent=1)
    if not rows:
        return
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    labs = [r["label"] for r in rows]
    y = np.arange(len(rows))
    for a, k, t, c in ((ax[0], "detection", "Fault detection", "#1f4e8c"), (ax[1], "false_approval", "False approval", "#d92d20"),
                       (ax[2], "safe_approval", "Safe changes auto-approved", "#12b76a")):
        v = [r[k] for r in rows]
        a.barh(y, v, color=[c if i else "#344054" for i in range(len(rows))])
        a.set_yticks(y, labs, fontsize=8)
        a.invert_yaxis()
        a.set_xlim(0, 1.1)
        a.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        for i, x in enumerate(v):
            a.text(x + .01, i, f"{x:.0%}", va="center", fontsize=7.5)
        a.set_title(t)
    save(fig, "fig_ablation")


def tier_attribution():
    """Which tier produced the failing evidence for each blocked unsafe case (agent, clear)."""
    rs = get("agent_hosted", intents=("clear",))
    cnt = Counter()
    by_variant = defaultdict(Counter)
    for r in rs:
        if r["label"] != "unsafe" or r["verdict"] != "block":
            continue
        tiers = {x["tier"] for x in r["results"] if x["status"] == "fail"}
        t = "both" if tiers == {"t1", "t2"} else ("Tier 1 (static)" if tiers == {"t1"} else "Tier 2 (emulation)")
        cnt[t] += 1
        by_variant[r["variant"]][t] += 1
    OUT["tiers"] = {"overall": dict(cnt), "by_variant": {k: dict(v) for k, v in by_variant.items()}}
    # oracle: which unsafe cases are visible to Tier 1 at all (B4 detects or escalates?)
    b4 = {r["case"]: r for r in get("B4_batfish_only", intents=("clear",))}
    t2only = [c for c in D.split("test") if c["label"] == "unsafe" and b4.get(c["id"], {}).get("verdict") == "approve"]
    OUT["tiers"]["t1_invisible_unsafe"] = len(t2only)
    OUT["tiers"]["t1_invisible_variants"] = dict(Counter(c["variant"] for c in t2only))
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    ks = ["Tier 1 (static)", "Tier 2 (emulation)", "both"]
    ax.bar(ks, [cnt[k] for k in ks], color=["#0e9384", "#7a5af8", "#1f4e8c"])
    for i, k in enumerate(ks):
        ax.text(i, cnt[k] + .3, cnt[k], ha="center")
    ax.set_ylabel("unsafe changes blocked")
    ax.set_title("Tier that produced the blocking evidence\n(proposed agent, clear tickets)")
    save(fig, "fig_tiers")


# =========================================================================== failure analysis
def failure_taxonomy(method="agent_hosted"):
    rs = get(method)
    wrong = [r for r in rs if (r["label"] == "unsafe" and r["verdict"] == "approve") or (r["label"] == "safe" and r["verdict"] == "block")
             or (r["label"] == "safe" and r["verdict"] == "escalate")]
    cats = []
    for r in wrong:
        tr = {t["step"]: t for t in r["trace"]}
        ia = tr.get("intent_analyzer", {}).get("response") or {}
        labels = {}
        for k, t in tr.items():
            if k.startswith("test_planner") and isinstance(t.get("response"), dict):
                labels.update(t["response"].get("labels") or {})
        fails = [x for x in r["results"] if x["status"] == "fail"]
        errs = [x for x in r["results"] if x["status"] == "error"]
        if r["label"] == "unsafe":
            reasons = D.by_id[r["case"]]["oracle_reasons"]
            t2only = bool(reasons) and all(any(k in x for k in ("failover", "% >", "byte DF", "drained")) for x in reasons)
            if t2only:
                cat = "Missed dependency (Tier-2 risk not flagged by impact analysis)"
            elif any(v == "intended" for v in labels.values()):
                cat = "Intent misread (side effect labelled intended)"
            elif r["intent"] != "clear" and not ia.get("expected_effects"):
                cat = "Missing intent (vague/terse ticket, no expectation to test)"
            else:
                cat = "Missed dependency / no test for the faulty effect"
        elif r["verdict"] == "block":
            if any(x["test"].get("t") == "inv" for x in fails):
                cat = "Judge rule gap (invariant)"
            elif errs:
                cat = "Tool / test error"
            else:
                cat = "Bad test (wrong expectation on intended effect)"
        else:
            if "unsure" in r["reason"] or "does not state" in r["reason"]:
                cat = "Escalated: ambiguous ticket (by design)"
            elif "invalid tests" in r["reason"]:
                cat = "Escalated: invalid tests after replans"
            else:
                cat = "Escalated: insufficient evidence"
        cats.append({"case": r["case"], "intent": r["intent"], "label": r["label"], "verdict": r["verdict"], "variant": r["variant"],
                     "category": cat, "reason": r["reason"][:200]})
    OUT["failures"] = cats
    json.dump(cats, open("results/failures.json", "w"), indent=1)
    lrs = get("agent_local")
    OUT["failures_local_count"] = len([r for r in lrs if (r["label"] == "unsafe" and r["verdict"] == "approve")
                                       or (r["label"] == "safe" and r["verdict"] != "approve")])
    cnt = Counter(c["category"] for c in cats)
    if not cnt:
        return
    fig, ax = plt.subplots(figsize=(8.5, 3.4))
    ks = [k for k, _ in cnt.most_common()]
    ax.barh(ks[::-1], [cnt[k] for k in ks][::-1], color="#f79009")
    for i, k in enumerate(ks[::-1]):
        ax.text(cnt[k] + .2, i, cnt[k], va="center", fontsize=8)
    ax.set_title("Failure taxonomy of the proposed agent (hosted, all ticket versions, test split)")
    ax.tick_params(axis="y", labelsize=8)
    save(fig, "fig_failures")


def fig_cost():
    ms = [m for m in ("agent_local", "agent_hosted", "B3_oneshot_local", "B3_oneshot_hosted") if get(m)]
    if not ms:
        return
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.3))
    for a, k, t in ((ax[0], "llm_seconds", "LLM time per change (s)"), (ax[1], "tokens", "tokens per change"), (ax[2], "cost_usd", "API cost per change (USD)")):
        vals = []
        for m in ms:
            rs = get(m, intents=("clear",))
            if k == "tokens":
                vals.append([r["tokens_in"] + r["tokens_out"] for r in rs])
            else:
                vals.append([r[k] for r in rs])
        bp = a.boxplot(vals, patch_artist=True, showfliers=False)
        for p, m in zip(bp["boxes"], ms):
            p.set_facecolor(COL[m])
            p.set_alpha(.75)
        a.set_xticks(range(1, len(ms) + 1), [SHORT[m] for m in ms], rotation=15, fontsize=8)
        a.set_title(t)
        a.grid(alpha=.3, axis="y")
    save(fig, "fig_cost")


def fig_literature():
    rows = OUT.get("main", [])
    ag = next((r for r in rows if r["method"] == "agent_local" and r["intent"] == "clear"), None)
    hs = next((r for r in rows if r["method"] == "agent_hosted" and r["intent"] == "clear"), None)
    if not hs:
        return
    if not ag:
        ag = dict(hs, detection=np.nan, ci_seconds=np.nan)
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.4))
    names = ["Aether [1]\n(8 scenarios)", "Ours: agent\n(local 7B)", "Ours: agent\n(hosted)"]
    det = [0.94, ag["detection"], hs["detection"] if hs else np.nan]
    tm = [6.5, ag["ci_seconds"] / 60, (hs["ci_seconds"] / 60) if hs else np.nan]
    ax[0].bar(names, det, color=["#98a2b3", C["agent"], C["hosted"]])
    ax[0].set_ylim(0, 1.08)
    ax[0].yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax[0].set_title("Faulty-change detection")
    ax[1].bar(names, tm, color=["#98a2b3", C["agent"], C["hosted"]])
    ax[1].set_title("Time per change (min)")
    for a, vs, f in ((ax[0], det, "{:.0%}"), (ax[1], tm, "{:.1f}")):
        for i, v in enumerate(vs):
            if not np.isnan(v):
                a.text(i, v * 1.01 + .01, f.format(v), ha="center", fontsize=8)
    save(fig, "fig_literature")


if __name__ == "__main__":
    fig_dataset()
    fig_topologies()
    fig_architecture()
    rows = main_table()
    if rows:
        fig_main_rates(rows)
        fig_time_vs_detection(rows)
        fig_tests_time_box()
        fig_intent_quality(rows)
        fig_per_class()
        fig_confusion()
        stats_tables()
        ablations()
        tier_attribution()
        failure_taxonomy()
        fig_cost()
        fig_literature()
    json.dump(OUT, open("results/all_results.json", "w"), indent=1, default=str)
    print("figures:", sorted(os.listdir("figures")))
