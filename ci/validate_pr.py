"""CI/CD gate: validate a network-config pull request with the agentic validator.

Usage (inside a CI job, repository = network source of truth):
    python ci/validate_pr.py --base <dir of main branch> --head <dir of PR branch> \
        --ticket <file with PR description> [--model local|hosted] [--out verdict.json] [--comment comment.md]

Exit codes: 0 = approve, 1 = block, 2 = escalate to a human reviewer.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from netval.model import Snapshot                       # noqa: E402
from netval.agent import AgenticValidator               # noqa: E402
from netval.llm import get_llm                          # noqa: E402

EXIT = {"approve": 0, "block": 1, "escalate": 2}
BADGE = {"approve": "APPROVED", "block": "BLOCKED", "escalate": "NEEDS HUMAN REVIEW"}


def load_repo(d: str) -> Snapshot:
    cfg = {os.path.basename(p)[:-4]: open(p).read() for p in sorted(glob.glob(os.path.join(d, "configs", "*.cfg")))}
    j = lambda f: json.load(open(os.path.join(d, f)))
    return Snapshot(cfg, j("inventory/hosts.json"), j("inventory/links.json"), j("policy/intent_policy.json"),
                    j("telemetry/traffic_matrix.json"))


def write_repo(s: Snapshot, d: str):
    for f, txt in s.files().items():
        p = os.path.join(d, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(txt)


def comment_md(v, ticket: str) -> str:
    L = [f"## Network change validation: **{BADGE[v.verdict]}**", "",
         f"> {ticket.strip()[:300]}", "", f"**Reason:** {v.reason}", "",
         f"Tests run: {len(v.tests)} | impact coverage: {v.coverage:.0%} | replans: {v.replans} | "
         f"modelled CI time: {v.ci_seconds:.0f} s (tools {v.tool_seconds:.0f} s + LLM {v.llm_seconds:.0f} s)", "",
         "| # | Test | Tier | Result | Evidence |", "|---|---|---|---|---|"]
    for i, r in enumerate(v.results[:40], 1):
        t = {k: x for k, x in r["test"].items()}
        icon = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skipped": "SKIP"}[r["status"]]
        L.append(f"| {i} | `{json.dumps(t)}` | {r.get('tier') or '-'} | {icon} | {str(r.get('detail',''))[:90]} |")
    L += ["", "<details><summary>Agent trace</summary>", "", "```json",
          json.dumps([{k: x for k, x in st.items() if k != 'impact'} for st in v.trace], indent=1)[:4000], "```", "</details>"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--model", default="local")
    ap.add_argument("--out", default="verdict.json")
    ap.add_argument("--comment", default="comment.md")
    a = ap.parse_args()
    pre, post = load_repo(a.base), load_repo(a.head)
    ticket = open(a.ticket).read()
    v = AgenticValidator(get_llm(a.model)).validate(pre, post, ticket)
    json.dump(v.to_dict(), open(a.out, "w"), indent=1, default=str)
    open(a.comment, "w").write(comment_md(v, ticket))
    print(f"verdict: {v.verdict.upper()} - {v.reason}")
    sys.exit(EXIT[v.verdict])


if __name__ == "__main__":
    main()
