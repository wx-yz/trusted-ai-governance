#!/usr/bin/env bash
# Everything the demo needs on your laptop, in one terminal:
#   http://localhost:3000   the demo UI (chat + dashboard), port-forwarded from the cluster
#   http://localhost:8090   the Salesforce audit feed for the dashboard (private admin port)
#   a public https URL      for the MCP endpoint ONLY. The Agent Manager API refuses MCP proxy upstreams whose host
#                           resolves to a private address (*.svc.cluster.local, localhost, 10.x ...), so the MCP
#                           port is published through a tunnel. /audit and /admin/reset are never published.
#
# Port-forwards die when their pod is replaced (for example when deploy-mcp.sh runs again), so each one runs in a
# loop that reconnects. Keep this running while you use the demo.
# Needs cloudflared (brew install cloudflared). Any other tunnel works too: point it at localhost:18080 and use its
# https URL plus /mcp as MCP_PUBLIC_URL.
# A quick tunnel gets a NEW url every time this script starts. The script warns when that happens.
# For a url that never changes, run your own tunnel to localhost:18080 (for example an ngrok static domain) and start
# this script with OWN_TUNNEL_URL=https://your-fixed-host. It then skips cloudflared and only does the port-forwards.
set -euo pipefail
NS=sales-demo
HERE="$(cd "$(dirname "$0")" && pwd)"
LAST_FILE="$HERE/.last-mcp-url"

[ -n "${OWN_TUNNEL_URL:-}" ] || command -v cloudflared >/dev/null || { echo "cloudflared not found. Install it (brew install cloudflared), or run your own tunnel to localhost:18080 and set OWN_TUNNEL_URL."; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl not found."; exit 1; }

# 1. The deployed server must be the two-port version, or publishing it would publish /audit and /admin/reset too.
PORTS="$(kubectl -n "$NS" get svc salesforce-mcp -o jsonpath='{.spec.ports[*].port}' 2>/dev/null || true)"
if [ -z "$PORTS" ]; then
  echo "Service salesforce-mcp not found in namespace $NS (kubectl context: $(kubectl config current-context 2>/dev/null || echo unknown))."
  echo "Run ./deploy/deploy-mcp.sh first, and check kubectl points at the amp-local cluster."
  exit 1
fi
case " $PORTS " in
  *" 8081 "*) ;;
  *) echo "The deployed MCP server is the old single-port version (service ports: $PORTS)."
     echo "Publishing it would also publish the audit log and reset. Run ./deploy/deploy-mcp.sh to update it, then run this again."
     exit 1 ;;
esac

PIDS=""
cleanup() {
  [ -n "$PIDS" ] && kill $PIDS 2>/dev/null || true       # supervisors first, so nothing respawns
  pkill -f "port-forward svc/salesforce-mcp" 2>/dev/null || true
  pkill -f "port-forward svc/governance-console" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 2. Clear port-forwards left behind by an earlier run: they hold the ports and hide the new ones.
pkill -f "port-forward svc/salesforce-mcp" 2>/dev/null || true
pkill -f "port-forward svc/governance-console" 2>/dev/null || true
sleep 1

forward() { # forward <service> <local:remote> <logfile>: keeps reconnecting if the pod is replaced
  ( while true; do kubectl -n "$NS" port-forward "svc/$1" "$2" >>"$3" 2>&1 || true; sleep 2; done ) &
  PIDS="$PIDS $!"
}
wait_for() { # wait_for <url> <seconds> <label> [logfile]
  local i
  for i in $(seq 1 "$2"); do curl -fsS -m 2 "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "ERROR: $3 is not answering at $1"
  [ -n "${4:-}" ] && { echo "--- kubectl said:"; tail -5 "$4"; }
  return 1
}

AUDIT_LOG="$(mktemp)"; MCP_LOG="$(mktemp)"; UI_LOG="$(mktemp)"
forward salesforce-mcp 8090:8081 "$AUDIT_LOG"      # audit and admin, private
forward salesforce-mcp 18080:8080 "$MCP_LOG"       # MCP only, this is the one that gets published
wait_for http://localhost:8090/catalog 20 "the audit port-forward (localhost:8090)" "$AUDIT_LOG" || {
  echo "Is another process using port 8090?  lsof -nP -iTCP:8090 -sTCP:LISTEN"; exit 1; }
wait_for http://localhost:18080/healthz 20 "the MCP port-forward (localhost:18080)" "$MCP_LOG" || exit 1
echo "audit feed ok:  http://localhost:8090"
echo "MCP port ok:    http://localhost:18080"

# The demo UI. Skipped if something already serves it on :3000 (for example python3 -m http.server).
if curl -fsS -m 2 http://localhost:3000/index.html >/dev/null 2>&1; then
  echo "demo UI ok:     http://localhost:3000 (already being served, leaving it alone)"
elif kubectl -n "$NS" get svc governance-console >/dev/null 2>&1; then
  forward governance-console 3000:80 "$UI_LOG"
  if wait_for http://localhost:3000/index.html 20 "the demo UI port-forward (localhost:3000)" "$UI_LOG"; then
    echo "demo UI ok:     http://localhost:3000"
  else
    echo "(continuing without the cluster UI. Serve it locally instead: cd governance-console && python3 -m http.server 3000)"
  fi
else
  echo "demo UI:        no governance-console service. Serve it locally: cd governance-console && python3 -m http.server 3000"
fi

# 3. The tunnel
if [ -n "${OWN_TUNNEL_URL:-}" ]; then
  URL="${OWN_TUNNEL_URL%/}"; URL="${URL%/mcp}"
  echo "using your own tunnel: $URL (it must forward to http://localhost:18080)"
else
  LOG="$(mktemp)"
  cloudflared tunnel --no-autoupdate --url http://localhost:18080 >"$LOG" 2>&1 & PIDS="$PIDS $!"
  URL=""
  for _ in $(seq 1 40); do
    URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
    [ -n "$URL" ] && break
    sleep 1
  done
  [ -n "$URL" ] || { echo "No tunnel URL appeared. cloudflared output:"; cat "$LOG"; exit 1; }
fi

# A new quick tunnel can take a while before its name resolves from this machine.
if ! wait_for "$URL/healthz" 90 "the tunnel"; then
  echo "(the tunnel name may still be propagating. It is usually fine a minute later.)"
fi

PREVIOUS="$(cat "$LAST_FILE" 2>/dev/null || true)"
echo "$URL/mcp" > "$LAST_FILE"

cat <<MSG

MCP server published (only /mcp and /healthz are reachable from outside, /mcp needs the API key).

  export MCP_PUBLIC_URL=$URL/mcp

Demo UI: http://localhost:3000     Audit feed: http://localhost:8090 (the UI's default)
MSG
if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$URL/mcp" ]; then
  cat <<MSG

!! The tunnel URL CHANGED since the last run:
     was  $PREVIOUS
     now  $URL/mcp
   Anything still using the old URL fails until you update it, in two places:
     1. the Salesforce MCP proxy: Console, MCP Servers > Salesforce MCP > Manage Endpoints
     2. the ungoverned agent: Deploy > Configure, SF_MCP_URL, then redeploy
MSG
fi
echo
echo "Leave this running. Press Ctrl+C to stop everything."
wait
