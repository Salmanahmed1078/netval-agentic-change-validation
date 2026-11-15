"""CLI: python run.py METHOD [split=test|tune|all] [repeats=1,2,3] [workers=N] [intents=clear,vague,terse] [limit=N]"""
import sys
from netval.experiment import Data, run, INTENTS
args = dict(a.split("=", 1) for a in sys.argv[2:])
data = Data()
cases = data.split(args.get("split", "test"))
if "only" in args:
    ids = set(args["only"].split(","))
    cases = [c for c in cases if c["id"] in ids]
if "limit" in args:
    cases = cases[: int(args["limit"])]
reps = tuple(int(x) for x in args.get("repeats", "1").split(","))
intents = args.get("intents", ",".join(INTENTS)).split(",")
run(sys.argv[1], data, cases, intents=intents, repeats=reps, workers=int(args.get("workers", 4)))
