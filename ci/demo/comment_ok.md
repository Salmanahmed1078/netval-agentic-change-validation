## Network change validation: **APPROVED**

> Staging needs database access: on leaf3 add '25 permit tcp 10.101.2.0/24 10.103.1.0/24 eq 5432' to ACL_DBA_OUT (before the final deny). Only stage-a1 -> db-a1 on tcp/5432 should open.

**Reason:** all 10 tests passed, coverage 100%

Tests run: 10 | impact coverage: 100% | replans: 0 | modelled CI time: 50 s (tools 43 s + LLM 7 s)

| # | Test | Tier | Result | Evidence |
|---|---|---|---|---|
| 1 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/5432", "expect": "allow"}` | t1 | PASS | all allow |
| 2 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/22", "expect": "unchanged"}` | t1 | PASS | unchanged |
| 3 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "icmp", "expect": "unchanged"}` | t1 | PASS | unchanged |
| 4 | `{"t": "reach", "src": "stage-b1", "dst": "db-a1", "svc": "tcp/5432", "expect": "unchanged"}` | t1 | PASS | unchanged |
| 5 | `{"t": "reach", "src": "web-a1", "dst": "db-a1", "svc": "tcp/5432", "expect": "unchanged"}` | t1 | PASS | unchanged |
| 6 | `{"t": "reach", "src": "wan-user1", "dst": "db-a1", "svc": "tcp/5432", "expect": "deny"}` | t1 | PASS | all deny |
| 7 | `{"t": "inv", "name": "no_loops"}` | t1 | PASS | no loops |
| 8 | `{"t": "inv", "name": "mgmt_reachable"}` | t1 | PASS | all managed devices reachable |
| 9 | `{"t": "inv", "name": "isolation"}` | t1 | PASS | isolation holds |
| 10 | `{"t": "inv", "name": "no_leak"}` | t1 | PASS | no internal prefixes leaked |

<details><summary>Agent trace</summary>

```json
[
 {
  "step": "intent_analyzer",
  "prompt_chars": 330,
  "response": {
   "ticket_says": "On leaf3, add ACL entry '25 permit tcp 10.101.2.0/24 10.103.1.0/24 eq 5432' to ACL_DBA_OUT before the final deny, so that staging host stage-a1 can reach db-a1 on tcp/5432 and nothing else opens.",
   "goal": "Allow the staging subnet to reach the database subnet on PostgreSQL (tcp/5432) via ACL_DBA_OUT on leaf3, specifically opening only stage-a1 -> db-a1 on tcp/5432.",
   "change_type": "acl",
   "scope": [
    "leaf3",
    "ACL_DBA_OUT",
    "stage-a1",
    "db-a1",
    "10.101.2.0/24",
    "10.103.1.0/24"
   ],
   "expected_effects": [
    "stage-a1 can open tcp/5432 connections to db-a1 through leaf3",
    "ACL_DBA_OUT contains a permit entry at sequence 25 for tcp 10.101.2.0/24 -> 10.103.1.0/24 eq 5432",
    "The new entry is placed before the final deny in ACL_DBA_OUT"
   ],
   "must_not_change": [
    "No other source/destination pairs or ports become permitted",
    "Traffic other than stage-a1 -> db-a1 on tcp/5432 keeps its existing behavior",
    "The final deny in ACL_DBA_OUT remains in effect for everything else"
   ],
   "confidence": "high"
  },
  "latency": 3.64,
  "cached": true
 },
 {
  "step": "impact_analyzer",
  "entries": 1,
  "behavioural": 1
 },
 {
  "step": "test_planner#0",
  "prompt_chars": 3143,
  "response": {
   "labels": {
    "I1": "intended"
   },
   "extra_tests": [
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "tcp/5432",
     "expect": "allow"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "tcp/22",
     "expect": "unchanged"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "icmp",
     "expect": "unchanged"
    },
    {
     "t": "reach",
     "src": "stage-b1",
     "dst": "db-a1",
     "svc": "tcp/5432",
     "expect": "unchanged"
    },
    {
     "t": "reach",
     "src": "web-a1",
     "dst": "db-a1",
     "svc": "tcp/5432",
     "expect": "unchanged"
    },
    {
     "t": "reach",
     "src": "wan-user1",
     "dst": "db-a1",
     "svc": "tcp/5432",
     "expect": "deny"
    }
   ]
  },
  "latency": 2.99,
  "cached": true
 },
 {
  "step": "executor+judge#0",
  "tests": 10,
  "failed": 0,
  "errors": 0,
  "coverage": 1.0,
  "unsure": []
 }
]
```
</details>