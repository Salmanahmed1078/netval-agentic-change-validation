#!/bin/zsh
set -e
cd "$(dirname "$0")"
.venv/bin/python run.py agent_local split=test repeats=1 workers=2 intents=clear only=$(cat local_ids.txt)
echo LOCAL_DONE
