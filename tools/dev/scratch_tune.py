import json, time, sys, collections
from concurrent.futures import ThreadPoolExecutor
from netval.experiment import Data
from netval.agent import AgenticValidator
from netval.llm import get_llm
d = Data(); ag = AgenticValidator(get_llm(sys.argv[1] if len(sys.argv)>1 else "local"))
cases = d.split("tune")
jobs=[(c,i) for c in cases for i in ["clear","vague","terse"]]
def one(j):
    c,i=j; pre_t, post_t = d.twins(c)
    v = ag.validate(d.bases[c["topology"]], post_t.s, c["tickets"][i], seed=1, tpre=pre_t, tpost=post_t)
    return c,i,v
t=time.time(); out=list(ThreadPoolExecutor(int(sys.argv[2]) if len(sys.argv)>2 else 4).map(one, jobs))
print(f"{len(jobs)} runs {time.time()-t:.0f}s")
cnt=collections.Counter((i,c["label"],v.verdict) for c,i,v in out)
for i in ["clear","vague","terse"]:
    print(i, {k[1:]:n for k,n in cnt.items() if k[0]==i})
for c,i,v in out:
    bad = (c["label"]=="unsafe" and v.verdict=="approve") or (c["label"]=="safe" and v.verdict=="block")
    if bad: print("  WRONG", c["id"], c["variant"], i, v.verdict, "|", v.reason[:160])
