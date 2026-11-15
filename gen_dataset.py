"""Generate the labelled change-case dataset -> data/dataset.json"""
import json, os, sys, time, collections
from netval.cases import generate
t0 = time.time()
d = generate(seed=int(sys.argv[1]) if len(sys.argv) > 1 else 7)
os.makedirs("data", exist_ok=True)
json.dump(d, open("data/dataset.json", "w"))
c = d["cases"]
print(f"total {len(c)} cases in {time.time()-t0:.1f}s")
print(collections.Counter((x["topology"], x["label"]) for x in c))
print(collections.Counter((x["cls"], x["label"]) for x in c))
print(collections.Counter(x["variant"] for x in c))
