#!/usr/bin/env bash
# Find out why an agent gets HTTP 404 from its LLM provider.
#
# An agent does not call the provider directly. When you attach a provider to an agent, Agent Manager creates a
# per-agent LLM proxy with a random context path (/<uuid>) in front of the provider, and injects LLM_PROVIDER_URL
# pointing at that proxy. A 404 means one of: the proxy has no live route on the gateway, OpenAI refuses the model for
# your key, or something on the agent side. This script tells them apart.
#
# Needs amctl (logged in) and jq. Export OPENAI_API_KEY too, to test your key and model directly.
# No gateway key is needed: a route that exists answers 401/400, a missing route answers 404.
set -uo pipefail

ORG="${ORG:-default}"
ENVIRONMENT="${ENVIRONMENT:-default}"
PROJECT="${PROJECT:-support-demo}"
PROVIDER="${PROVIDER:-shared-openai}"
MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
OPENAI_BASE="${OPENAI_BASE:-https://api.openai.com/v1}"
command -v amctl >/dev/null && command -v jq >/dev/null || { echo "amctl and jq are required"; exit 1; }

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
BODY_FILE="$(mktemp)"; trap 'rm -f "$BODY_FILE"' EXIT
http() { curl -s -m 40 -o "$BODY_FILE" -w '%{http_code}' "$@" || echo 000; }
snippet() { head -c "${1:-200}" "$BODY_FILE" | tr '\n' ' '; }
exists() { case "$1" in 401|403|400|405|415|422|200|429|500|502|503) return 0 ;; *) return 1 ;; esac; }
route_exists() { # route_exists <url>: prints the code, returns 0 when a route answers
  local code; code="$(http -X POST "$1" -H 'Content-Type: application/json' -d '{}')"; LAST_CODE="$code"
  if exists "$code" || { [ "$code" = "404" ] && grep -qi 'model' "$BODY_FILE"; }; then return 0; fi; return 1
}

say "Provider '$PROVIDER'"
P="$(amctl api "/orgs/$ORG/llm-providers/$PROVIDER" 2>&1)" || { echo "$P"; echo "Provider not found. Is PROVIDER right?"; exit 1; }
jq -r '[paths(scalars) as $p | {k: ($p | map(tostring) | join(".")), v: getpath($p)}]
       | map(select(.k | test("(context|version|vhost|template|mode|upstream.*url|access|security|apikey)"; "i")))
       | .[] | "  \(.k) = \(.v)"' <<<"$P"
CTX="$(jq -r '[.. | objects | .context? | strings] | first // "/"' <<<"$P")"; [ "$CTX" = "/" ] && CTX=""
SECURED="$(jq -r '[.. | objects | .apiKey? | select(type=="object") | .enabled?] | first // false' <<<"$P")"
[ "$SECURED" = "true" ] || echo "  note: API key security is not enabled on this provider (the CLI does not turn it on, the Console does)"

say "Gateway for environment '$ENVIRONMENT'"
G="$(amctl gateway list --env "$ENVIRONMENT" --json 2>/dev/null || true)"
VHOST="${VHOST:-$(jq -r '[(.data.gateways // .gateways // [])[] | select((.gatewayType // "BOTH") | test("EGRESS|BOTH"; "i"))] | first | .vhost // empty' <<<"$G")}"
GWID="$(jq -r '[(.data.gateways // .gateways // [])[]] | first | .uuid // empty' <<<"$G")"
RUNTIME="$(jq -r '[(.data.gateways // .gateways // [])[]] | first | .runtimeUrl // empty' <<<"$G")"
[ -n "$VHOST" ] || { echo "Could not find the gateway vhost. Run: amctl gateway list --env $ENVIRONMENT   then re-run with VHOST=http://..."; exit 1; }
VHOST="${VHOST%/}"
echo "  public vhost:  $VHOST"
echo "  agents reach it in-cluster at: ${RUNTIME:-unknown}"

say "1. The provider's own route (agents do not call this one, but the proxy forwards to it)"
if route_exists "$VHOST${CTX}/chat/completions"; then echo "  ${CTX}/chat/completions   HTTP $LAST_CODE   route exists"; PROVIDER_ROUTE=yes
else echo "  ${CTX}/chat/completions   HTTP $LAST_CODE   NO ROUTE"; PROVIDER_ROUTE=no; fi
route_exists "$VHOST/commerce/mcp" && echo "  control: /commerce/mcp   HTTP $LAST_CODE   route exists (the gateway itself is up)" || echo "  control: /commerce/mcp   HTTP $LAST_CODE   no route"

say "2. The LLM proxies Agent Manager created in front of the provider (these are what agents call)"
PR="$(amctl api "/orgs/$ORG/llm-providers/$PROVIDER/llm-proxies" 2>&1 || true)"
PROXIES="$(jq -r '[.. | objects | select((.id? // .handle?) != null)
                   | select(((.configuration?.context? // .context?) | type) == "string")
                   | {id: (.id // .handle), context: (.configuration?.context? // .context)}]
                  | map(select(.context | length > 1)) | unique | .[] | "\(.id)\t\(.context)"' <<<"$PR" 2>/dev/null)"
PROXY_COUNT=0; MISSING=""
if [ -z "$PROXIES" ]; then
  echo "  none found. $(head -c 200 <<<"$PR" | tr '\n' ' ')"
else
  while IFS=$'\t' read -r PID PCTX; do
    [ -n "$PID" ] || continue
    PROXY_COUNT=$((PROXY_COUNT + 1))
    DEP="$(amctl api "/orgs/$ORG/projects/$PROJECT/llm-proxies/$PID/deployments" 2>/dev/null || true)"
    DSTATE="$(jq -r '[.. | objects | .status? | strings] | unique | join(",")' <<<"$DEP" 2>/dev/null)"
    if route_exists "$VHOST$PCTX/chat/completions"; then R="route exists"; else R="NO ROUTE"; MISSING="$MISSING $PID"; fi
    printf '  proxy %-34s %s/chat/completions  HTTP %-4s %s  (deployments: %s)\n' "$PID" "$PCTX" "$LAST_CODE" "$R" "${DSTATE:-none}"
  done <<<"$PROXIES"
fi

say "3. Does OpenAI accept your key and model '$MODEL'? (asks $OPENAI_BASE directly)"
OPENAI_CODE=skip
if [ -n "${OPENAI_API_KEY:-}" ]; then
  OPENAI_CODE="$(http -X POST "$OPENAI_BASE/chat/completions" -H 'Content-Type: application/json' -H "Authorization: Bearer $OPENAI_API_KEY" \
    -d "$(jq -nc --arg m "$MODEL" '{model:$m, messages:[{role:"user", content:"Reply with the single word ok."}], max_tokens:5}')")"
  echo "  HTTP $OPENAI_CODE  $(snippet 160)"
else
  echo "  skipped. Export OPENAI_API_KEY and run again to test your key and model directly."
fi

say "What this means"
if [ "$OPENAI_CODE" = "404" ]; then
  echo "  OpenAI itself answers 404: '$MODEL' is not available to this key. This is the cause."
  echo "  List what the key can use:  curl -s $OPENAI_BASE/models -H \"Authorization: Bearer \$OPENAI_API_KEY\" | jq -r '.data[].id' | grep -i gpt"
  echo "  Then set OPENAI_MODEL on BOTH agents (Deploy > Configure) and redeploy them."
elif [ "$OPENAI_CODE" = "401" ]; then
  echo "  OpenAI rejects the key. Fix it on the provider and on the ungoverned agent."
elif [ "$PROXY_COUNT" -eq 0 ]; then
  echo "  The provider has no LLM proxy, so the agent's LLM_PROVIDER_URL points at nothing."
  echo "  Console: support-agent > Configure > LLM Configurations: remove the configuration, add Shared OpenAI again, redeploy the agent."
elif [ -n "$MISSING" ]; then
  echo "  The proxy exists in Agent Manager but has no live route on the gateway:$MISSING"
  echo "  That is the 404. Deploy it to the gateway:"
  for PID in $MISSING; do
    echo "    jq -nc '{name:\"redeploy\",base:\"current\",gatewayId:\"$GWID\"}' | amctl api /orgs/$ORG/projects/$PROJECT/llm-proxies/$PID/deployments -X POST --input -"
  done
  echo "  If that does not fix it, remove and re-add the LLM configuration on the agent (Console), which rebuilds the proxy."
elif [ "$PROVIDER_ROUTE" = "no" ]; then
  echo "  The proxy route exists, but the provider's own route (${CTX}/chat/completions) does not, and the proxy forwards to it."
  echo "  Redeploy the provider in the Console (LLM Service Providers > $PROVIDER)."
else
  echo "  Every route the agent needs exists and OpenAI is fine. The 404 is not a missing route."
  echo "  Rebuild the agent with the latest code and send /diagnose: it prints the exact response body."
fi
