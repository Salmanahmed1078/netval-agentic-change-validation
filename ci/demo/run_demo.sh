#!/bin/zsh
# CI gate demo: PR that over-opens ACL_DBA_OUT (block) and the correct PR (approve)
cd "$(dirname "$0")/../.."
P=.venv/bin/python
echo "--- PR #42: 'Staging needs database access' (faulty implementation)"
$P ci/validate_pr.py --base ci/demo/base --head ci/demo/head --ticket ci/demo/ticket.txt --model hosted \
   --out ci/demo/verdict.json --comment ci/demo/comment.md; echo "exit code: $?"
echo "--- PR #43: same ticket, correct implementation"
$P ci/validate_pr.py --base ci/demo/base --head ci/demo/head_ok --ticket ci/demo/ticket.txt --model hosted \
   --out ci/demo/verdict_ok.json --comment ci/demo/comment_ok.md; echo "exit code: $?"
