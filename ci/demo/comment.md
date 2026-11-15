## Network change validation: **BLOCKED**

> Staging needs database access: on leaf3 add '25 permit tcp 10.101.2.0/24 10.103.1.0/24 eq 5432' to ACL_DBA_OUT (before the final deny). Only stage-a1 -> db-a1 on tcp/5432 should open.

**Reason:** {"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "*", "expect": "unchanged"} -> changed {'icmp': (False, True), 'tcp/22': (False, True), 'tcp/25': (False, True), 'tcp/80'; {"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "icmp", "expect": "deny"} -> violations ['icmp'] (ACCEPTED ); {"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/22", "expect": "deny"} -> violations ['tcp/22'] (ACCEPTED )

Tests run: 12 | impact coverage: 100% | replans: 0 | modelled CI time: 51 s (tools 45 s + LLM 6 s)

| # | Test | Tier | Result | Evidence |
|---|---|---|---|---|
| 1 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "*", "expect": "unchanged"}` | t1 | FAIL | changed {'icmp': (False, True), 'tcp/22': (False, True), 'tcp/25': (False, True), 'tcp/80' |
| 2 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/5432", "expect": "allow"}` | t1 | PASS | all allow |
| 3 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "icmp", "expect": "deny"}` | t1 | FAIL | violations ['icmp'] (ACCEPTED ) |
| 4 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/22", "expect": "deny"}` | t1 | FAIL | violations ['tcp/22'] (ACCEPTED ) |
| 5 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/80", "expect": "deny"}` | t1 | FAIL | violations ['tcp/80'] (ACCEPTED ) |
| 6 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/443", "expect": "deny"}` | t1 | FAIL | violations ['tcp/443'] (ACCEPTED ) |
| 7 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "tcp/25", "expect": "deny"}` | t1 | FAIL | violations ['tcp/25'] (ACCEPTED ) |
| 8 | `{"t": "reach", "src": "stage-a1", "dst": "db-a1", "svc": "udp/53", "expect": "deny"}` | t1 | FAIL | violations ['udp/53'] (ACCEPTED ) |
| 9 | `{"t": "inv", "name": "no_loops"}` | t1 | PASS | no loops |
| 10 | `{"t": "inv", "name": "mgmt_reachable"}` | t1 | PASS | all managed devices reachable |
| 11 | `{"t": "inv", "name": "isolation"}` | t1 | PASS | isolation holds |
| 12 | `{"t": "inv", "name": "no_leak"}` | t1 | PASS | no internal prefixes leaked |

<details><summary>Agent trace</summary>

```json
[
 {
  "step": "intent_analyzer",
  "prompt_chars": 311,
  "response": {
   "ticket_says": "On leaf3, add ACL entry '25 permit tcp 10.101.2.0/24 10.103.1.0/24 eq 5432' to ACL_DBA_OUT, positioned before the final deny, so that only stage-a1 can reach db-a1 on tcp/5432.",
   "goal": "Allow the staging host stage-a1 (10.101.2.0/24) to reach the database host db-a1 (10.103.1.0/24) on TCP port 5432 through ACL_DBA_OUT on leaf3, without opening anything else.",
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
    "stage-a1 (10.101.2.0/24) can open TCP connections to db-a1 (10.103.1.0/24) on port 5432",
    "ACL_DBA_OUT on leaf3 contains a new entry at sequence 25: permit tcp 10.101.2.0/24 10.103.1.0/24 eq 5432",
    "The new entry is placed before the final deny in ACL_DBA_OUT"
   ],
   "must_not_change": [
    "No other source, destination, protocol, or port is newly permitted by ACL_DBA_OUT",
    "The final deny entry in ACL_DBA_OUT remains in effect for all other traffic",
    "Existing ACL entries and other devices are unchanged"
   ],
   "confidence": "high"
  },
  "latency": 3.52,
  "cached": true
 },
 {
  "step": "impact_analyzer",
  "entries": 1,
  "behavioural": 1
 },
 {
  "step": "test_planner#0",
  "prompt_chars": 3210,
  "response": {
   "labels": {
    "I1": "unintended"
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
     "svc": "icmp",
     "expect": "deny"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "tcp/22",
     "expect": "deny"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "tcp/80",
     "expect": "deny"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "tcp/443",
     "expect": "deny"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "tcp/25",
     "expect": "deny"
    },
    {
     "t": "reach",
     "src": "stage-a1",
     "dst": "db-a1",
     "svc": "udp/53",
     "expect": "deny"
    }
   ]
  },
  "latency": 2.4,
  "cached": true
 },
 {
  "step": "executor+judge#0",
  "tests": 12,
  "failed": 7,
  "errors": 0,
  "coverage": 1.0,
  "unsure": []
 }
]
```
</details>