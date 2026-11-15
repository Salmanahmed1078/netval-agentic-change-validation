#!/bin/zsh
set -e
cd "$(dirname "$0")"
P=.venv/bin/python
for m in B1_full_suite B2_rule_based B4_batfish_only; do $P run.py $m split=all workers=4; done
$P run.py agent_hosted split=test repeats=1,2,3 workers=8
$P run.py B3_oneshot_hosted split=test repeats=1 workers=8
for m in abl_no_impact_hosted abl_no_invariants_hosted abl_no_feedback_hosted abl_tier1_only_hosted abl_tier2_only_hosted; do
  $P run.py $m split=test repeats=1 workers=8
done
echo HOSTED_DONE
