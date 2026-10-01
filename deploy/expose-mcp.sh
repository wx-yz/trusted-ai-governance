#!/usr/bin/env bash
# Publish ONLY the MCP port of the Salesforce mock through a public tunnel, because the Agent Manager API refuses
# MCP proxy upstreams whose host resolves to a private address (*.svc.cluster.local, localhost, 10.x, 192.168.x ...).
# The audit/admin port stays private and is port-forwarded to localhost:8090 for the dashboard.
#
# Needs cloudflared (brew install cloudflared). Any other tunnel works too: point it at localhost:18080 and use its
# https URL plus /mcp as MCP_PUBLIC_URL.
# Keep this running while you use the governed agent: the AI gateway reaches the MCP server through this tunnel.
set -euo pipefail
NS=sales-demo
command -v cloudflared >/dev/null || { echo "cloudflared not found. Install it (brew install cloudflared) or use another tunnel to localhost:18080."; exit 1; }

PIDS=""
cleanup() { [ -n "$PIDS" ] && kill $PIDS 2>/dev/null || true; }
trap cleanup EXIT INT TERM

kubectl -n "$NS" port-forward svc/salesforce-mcp 8090:8081 >/dev/null 2>&1 & PIDS="$PIDS $!"    # audit and admin, private
kubectl -n "$NS" port-forward svc/salesforce-mcp 18080:8080 >/dev/null 2>&1 & PIDS="$PIDS $!"   # MCP only, this is published
sleep 2

LOG="$(mktemp)"
cloudflared tunnel --no-autoupdate --url http://localhost:18080 >"$LOG" 2>&1 & PIDS="$PIDS $!"
URL=""
for _ in $(seq 1 40); do
  URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "No tunnel URL appeared. cloudflared output:"; cat "$LOG"; exit 1; }

for _ in $(seq 1 30); do curl -fsS "$URL/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "$URL/healthz" >/dev/null 2>&1 || { echo "Tunnel is up but $URL/healthz is not answering yet. Give it a few seconds."; }

cat <<MSG

MCP server published (only /mcp and /healthz are useful from outside, /mcp needs the API key).

  export MCP_PUBLIC_URL=$URL/mcp

Run that in the terminal where you run ./deploy/setup-agent-manager.sh. Leave this script running.
Dashboard audit feed: http://localhost:8090  (already the demo UI default)
MSG
wait
