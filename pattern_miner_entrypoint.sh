#!/bin/sh
# Cloud Run Jobs are run-to-completion: start Ollama, wait for it to be
# ready, run the miner, propagate its exit code, then let the container
# exit -- there's no long-lived server to keep alive between executions.
set -e

ollama serve &
OLLAMA_PID=$!

python3 -c "
import time, urllib.request
for _ in range(30):
    try:
        urllib.request.urlopen('http://localhost:11434/api/version', timeout=2)
        break
    except Exception:
        time.sleep(1)
"

python3 mine_patterns.py
CODE=$?

kill "$OLLAMA_PID" 2>/dev/null || true
exit $CODE
