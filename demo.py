"""Console demo: validate one change case and print every pipeline stage.

    .venv/bin/python demo.py D-MTU-1-F clear hosted
"""
import json
import sys
import time

from netval.experiment import Data
from netval.agent import AgenticValidator
from netval.baselines import FullSuite, RuleBased, BatfishOnly
from netval.impact import structured_diff
from netval.agent import render_diff
from netval.llm import get_llm

cid = sys.argv[1] if len(sys.argv) > 1 else "D-MTU-1-F"
intent = sys.argv[2] if len(sys.argv) > 2 else "clear"
model = sys.argv[3] if len(sys.argv) > 3 else "hosted"
d = Data()
c = d.by_id[cid]
pre_t, post_t = d.twins(c)
pre = d.bases[c["topology"]]
B = "\033[1m"
E = "\033[0m"
print(f"== change case {cid} ({c['topology']}, class {c['cls']}) | ticket version: {intent} | model: {model}")
print(f"ticket: {c['tickets'][intent]}")
print("diff:\n  " + render_diff(structured_diff(pre, post_t.s)).replace("\n", "\n  "))
t0 = time.time()
v = AgenticValidator(get_llm(model)).validate(pre, post_t.s, c["tickets"][intent], tpre=pre_t, tpost=post_t)
for st in v.trace:
    if st["step"] == "intent_analyzer":
        r = st["response"]
        print(f"\n[1] intent analyzer  -> confidence={r.get('confidence')} goal={r.get('goal')}")
    elif st["step"] == "impact_analyzer":
        print(f"[2] impact analyzer  -> {st['entries']} entries ({st['behavioural']} behavioural)")
        for line in st["impact"].splitlines()[:8]:
            print("      " + line[:130])
    elif st["step"].startswith("test_planner"):
        r = st["response"] if isinstance(st["response"], dict) else {}
        print(f"[3] {st['step']:<15} -> labels {json.dumps(r.get('labels', {}))[:110]}, extra tests {len(r.get('extra_tests', r.get('tests', [])))}")
    elif st["step"].startswith("executor"):
        print(f"[4/5] executor+judge -> {st['tests']} tests, {st['failed']} failed, {st['errors']} invalid, coverage {st['coverage']:.0%}")
print("\n   status  tier  test")
for r in v.results:
    print(f"   {r['status']:<7} {r.get('tier') or '-':<5} {json.dumps(r['test'])[:84]:<86} {str(r['detail'])[:50]}")
print(f"\nVERDICT: {v.verdict.upper()}  ({v.reason[:150]})")
print(f"tests={len(v.tests)} replans={v.replans} llm_calls={v.llm_calls} tokens={v.tokens_in + v.tokens_out} "
      f"cost=${v.cost_usd:.4f} modelled CI time={v.ci_seconds:.0f}s wall={time.time() - t0:.1f}s")
print(f"oracle ground truth: {c['label'].upper()}  ({'; '.join(c['oracle_reasons'][:2]) or 'intent achieved, nothing else changed'})")
print("\nbaselines on the same change:")
for M in (FullSuite(), RuleBased(), BatfishOnly()):
    b = M.validate(pre, post_t.s, tpre=pre_t, tpost=post_t)
    print(f"   {M.name:<18} {b.verdict:<9} tests={len(b.tests):<5} CI time={b.tool_seconds:.0f}s")
