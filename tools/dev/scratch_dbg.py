import random, sys
from netval.cases import CATALOG, Net
from netval.topologies import TOPOLOGIES
from netval.oracle import profile, oracle
want = sys.argv[1:]
rng = random.Random(7)
for topo, fn in TOPOLOGIES.items():
    base = fn(); pp = profile(base)
    T = CATALOG[topo](base, rng)
    print(topo, "templates:", [t.tid for t in T])
    for t in T:
        if want and t.tid not in want: continue
        if t.safe:
            n = Net(base); t.safe(n); o = oracle(pp, profile(n.snapshot()), t.intent, base.policy)
            if o["label"] != "safe": print(" SAFE-REJ", t.tid, o["reasons"][:5])
        for f, fn2 in t.faults.items():
            n = Net(base); fn2(n); o = oracle(pp, profile(n.snapshot()), t.intent, base.policy)
            if o["label"] != "unsafe": print("  fault-not-unsafe", t.tid, f)
