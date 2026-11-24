"""Poster/standee charts drawn from the logged Phase-3 results.

    .venv/bin/python poster/charts.py

Writes SVGs to poster/assets/ with text kept as text, so the browser renders it in the
poster typeface. Every number comes from results/all_results.json.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "poster", "assets")
os.makedirs(OUT, exist_ok=True)
R = json.load(open(os.path.join(ROOT, "results", "all_results.json")))

# ---- shared visual identity (keep in sync with poster/style.css) ----
INK = "#0E1A2B"
MUTED = "#5B6677"
GRID = "#E3E7EE"
NAVY = "#123A6B"
ACCENT = "#E07A1F"      # NetVal highlight
BASE = ["#B9C2CF", "#98A3B3", "#7A8698", "#5E6B7E"]  # baselines, light to dark
FAIL = "#C2372E"
OK = "#1E8A5A"
WARN = "#D99A1E"

plt.rcParams.update({
    "svg.fonttype": "none", "font.family": "sans-serif",
    "font.sans-serif": ["IBM Plex Sans", "Helvetica", "Arial"],
    "font.size": 8.5, "axes.edgecolor": "#C9D0DA", "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
    "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 9,
    "axes.titlecolor": INK, "axes.titlelocation": "left", "axes.titlepad": 10,
    "legend.frameon": False,
})


def row(method, intent="clear"):
    for r in R["main"]:
        if r["method"] == method and r["intent"] == intent:
            return r
    raise KeyError(method, intent)


def abl(name):
    for a in R["ablation"]:
        if a["method"] == name:
            return a
    raise KeyError(name)


METHODS = [("B1 Full suite", "B1_full_suite"), ("B2 Rule-based", "B2_rule_based"),
           ("B4 Static only", "B4_batfish_only"), ("B3 One-shot LLM", "B3_oneshot_hosted"),
           ("NetVal", "agent_hosted")]
COLORS = BASE + [ACCENT]


def save(fig, name):
    fig.savefig(os.path.join(OUT, name + ".svg"), bbox_inches="tight", transparent=True)
    plt.close(fig)


def _style_y(ax, labels, bold_last=True):
    ax.set_yticks(np.arange(len(labels))[::-1])
    ax.set_yticklabels(labels)
    ax.tick_params(axis="y", length=0)
    if bold_last:
        t = ax.get_yticklabels()[-1]
        t.set_fontweight("bold")
        t.set_color(INK)


def chart_tradeoff():
    """Safety (false approvals) vs automation (safe changes auto-approved), clear tickets."""
    fig, ax = plt.subplots(figsize=(3.9, 3.05))
    ax.add_patch(plt.Rectangle((-2, 75), 12, 30, color="#FCEBDA", zorder=0, lw=0))
    ax.text(0.5, 101, "ideal: safe and automated", fontsize=7.5, color=ACCENT, va="top", style="italic")
    offs = {"B1 Full suite": (6, -3), "B2 Rule-based": (-6, 7), "B4 Static only": (-6, -11),
            "B3 One-shot LLM": (6, -2), "NetVal": (6, -2)}
    has = {"B2 Rule-based": "right", "B4 Static only": "right"}
    for (label, m), col in zip(METHODS, COLORS):
        r = row(m)
        x, y = 100 * r["false_approval"], 100 * r["safe_approval"]
        nv = label == "NetVal"
        ax.scatter(x, y, s=150 if nv else 70, color=col, edgecolor="white", lw=1.2, zorder=3)
        ax.annotate(label, (x, y), textcoords="offset points", xytext=offs[label], fontsize=8,
                    ha=has.get(label, "left"), va="center", color=INK if nv else MUTED,
                    fontweight="bold" if nv else "normal")
    ax.set_xlim(-2, 46)
    ax.set_ylim(25, 104)
    ax.set_xlabel("False approvals: unsafe changes let through (%)  \u2190 better")
    ax.set_ylabel("Safe changes auto-approved (%)  better \u2192")
    ax.grid(color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    save(fig, "chart_tradeoff")


def chart_cost():
    """Modelled CI time per change, with the number of tests in the label."""
    fig, ax = plt.subplots(figsize=(3.9, 2.25))
    labels = [m for m, _ in METHODS]
    mins = [row(m)["ci_seconds"] / 60 for _, m in METHODS]
    tests = [row(m)["tests"] for _, m in METHODS]
    y = np.arange(len(METHODS))[::-1]
    bars = ax.barh(y, mins, color=COLORS, height=0.66)
    for b, v, t, lab in zip(bars, mins, tests, labels):
        nv = lab == "NetVal"
        ax.text(v + 0.3, b.get_y() + b.get_height() / 2, f"{v:.1f} min \u00b7 {t:,.0f} tests", va="center",
                fontsize=7.8, color=INK if nv else MUTED, fontweight="bold" if nv else "normal")
    ax.set_xlim(0, 31)
    ax.set_xlabel("Modelled CI time per change (min)")
    ax.grid(axis="x", color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    _style_y(ax, labels)
    fig.tight_layout()
    save(fig, "chart_cost")


def chart_tickets():
    """Ticket quality: NetVal false approvals and escalations vs the one-shot LLM."""
    levels = ["clear", "vague", "terse"]
    series = [("NetVal false approvals", "agent_hosted", "false_approval", ACCENT),
              ("One-shot LLM false approvals", "B3_oneshot_hosted", "false_approval", BASE[2]),
              ("NetVal escalated to a human", "agent_hosted", "escalation_rate", NAVY)]
    fig, ax = plt.subplots(figsize=(3.9, 2.55))
    x = np.arange(len(levels))
    w = 0.26
    for i, (lab, m, key, col) in enumerate(series):
        v = [100 * row(m, lv)[key] for lv in levels]
        bars = ax.bar(x + (i - 1) * w, v, w * 0.92, color=col, label=lab)
        for b, val in zip(bars, v):
            ax.text(b.get_x() + b.get_width() / 2, val + 1.5, f"{val:.0f}" if val >= 10 or val == 0 else f"{val:.1f}",
                    ha="center", fontsize=7.3, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{lv} ticket" for lv in levels], fontsize=8.5)
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of changes")
    ax.grid(axis="y", color=GRID, lw=0.7)
    ax.set_axisbelow(True)
    ax.legend(fontsize=7.3, loc="upper left", handlelength=1.0, borderaxespad=0.1)
    fig.tight_layout()
    save(fig, "chart_tickets")


if __name__ == "__main__":
    chart_tradeoff()
    chart_cost()
    chart_tickets()
    print("charts written to", OUT)
