#!/usr/bin/env bash
# AgentOS one-command demo.
#
# Prereq: server running → uvicorn agentos.interfaces.api:get_app --factory --port 8080
# Usage:  ./examples/demo.sh [port]
set -euo pipefail
PORT="${1:-8080}"
BASE="http://localhost:$PORT"

say() { printf '\n\033[1;35m== %s ==\033[0m\n' "$*"; }

say "1. What can this platform do? (GET /capabilities)"
curl -s "$BASE/capabilities" | python3 -c "
import json, sys
for c in json.load(sys.stdin):
    if c['kind'] == 'outcome_module':
        print(f\"  • {c['name']}: {c['description'][:70]}…\")"

say "2. Delegate an outcome (POST /goals → 202 + ExecutionID)"
EXEC=$(curl -s -X POST "$BASE/goals" \
  -H 'Content-Type: application/json' \
  -d '{"description": "Resolve all unresolved tier-1 tickets from last 24h", "requested_by": "demo"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['execution_id'])")
echo "  execution_id = $EXEC   ← returned immediately; nobody waits"

say "3. Poll for delivery (agents never watch progress bars)"
for i in $(seq 1 40); do
  STATUS=$(curl -s "$BASE/executions/$EXEC" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  [ "$STATUS" = "completed" ] && break
  sleep 0.1
done

say "4. The delivered outcome + full audit trail"
curl -s "$BASE/executions/$EXEC" | python3 -c "
import json, sys
d = json.load(sys.stdin)
o = d['outcome']
print(f\"  status : {d['status']}\")
print(f\"  summary: {o['summary']}\")
print(f\"  metrics: {o['metrics']}  validated={o['validated']}\")
print('  trace  :')
for e in d['trace']:
    print(f\"    [{e['kind']}] {e['message'][:70]}\")"

printf '\n\033[1;32mOutcome delivered, zero clicks.\033[0m Try: open %s/docs\n' "$BASE"
