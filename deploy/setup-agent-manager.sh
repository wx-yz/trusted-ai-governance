#!/usr/bin/env bash
# Configure WSO2 Agent Manager for the Trusted AI Governance demo with amctl.
#
# Creates: a project, the Shared OpenAI LLM provider, the Salesforce MCP proxy (OAuth, per-tool scopes),
# two roles, both agents, and the role assignment for the governed agent.
# Three things are left to the Console on purpose (guardrails, tool configuration, API keys); they are printed at the end.
#
# Needs: amctl (logged in), jq, and a PUBLIC url for the MCP server (run ./deploy/expose-mcp.sh first).
# Re-runnable: steps that already exist print a note and carry on. Steps everything else depends on stop the script.
set -uo pipefail
cd "$(dirname "$0")"

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY}"
: "${REPO_URL:?Set REPO_URL, for example https://github.com/<owner>/<repo>}"
: "${MCP_PUBLIC_URL:?Set MCP_PUBLIC_URL to the public https URL of the MCP server (run ./deploy/expose-mcp.sh)}"
REPO_BRANCH="${REPO_BRANCH:-main}"
REPO_DIR="${REPO_DIR-}"                        # folder of this demo inside the repo. Empty when the repo root is this folder.
ORG="${ORG:-default}"
ENVIRONMENT="${ENVIRONMENT:-default}"
PROJECT="${PROJECT:-sales-demo}"
PROVIDER="${PROVIDER:-shared-openai}"
MCP_DIRECT_URL="${MCP_DIRECT_URL:-http://salesforce-mcp.sales-demo.svc.cluster.local:8080/mcp}"  # ungoverned agent, in-cluster
GOVERNED="${GOVERNED:-sales-copilot}"
UNGOVERNED="${UNGOVERNED:-sales-copilot-ungoverned}"

say()   { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()   { printf '\n\033[1;31mSTOP: %s\033[0m\n' "$*" >&2; exit 1; }
soft()  { "$@" || echo "  (did not succeed, it may already exist; continuing)"; }
find_id() { # find_id <json> <name> <field...>: the first object called <name> that has one of the fields
  jq -r --arg n "$2" --argjson f "$(printf '%s\n' "${@:3}" | jq -R . | jq -sc .)" \
    '[.. | objects | select((.name? == $n) or (.handle? == $n) or (.agentName? == $n))
      | . as $o | ($f | map($o[.]) | map(select(. != null)) | first)] | map(select(. != null)) | first // empty' <<<"$1"
}

command -v amctl >/dev/null || die "amctl not found. Install: curl -fsSL https://wso2.github.io/agent-manager/install.sh | sh"
command -v jq >/dev/null || die "jq is required"

# ---- repository: Agent Manager wants exactly https://github.com/owner/repo
slug="${REPO_URL%/}"; slug="${slug%.git}"
for prefix in "git@github.com:" "ssh://git@github.com/" "https://github.com:" "https://github.com/" "http://github.com/" "github.com/"; do slug="${slug#"$prefix"}"; done
[[ "$slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "Cannot read REPO_URL '$REPO_URL'. Use https://github.com/<owner>/<repo>"
REPO_URL="https://github.com/$slug"
APP_PATH="/${REPO_DIR:+$REPO_DIR/}sales-copilot"

# ---- the MCP proxy upstream must resolve to a public address (the Agent Manager API enforces this)
host="${MCP_PUBLIC_URL#*://}"; host="${host%%/*}"; host="${host%%:*}"
if [[ "$MCP_PUBLIC_URL" != http*://* ]] || [[ "$host" =~ ^(localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[01])\.) ]] \
   || [[ "$host" =~ \.(local|localhost|internal|svc|cluster\.local)$ ]]; then
  die "MCP_PUBLIC_URL '$MCP_PUBLIC_URL' is not public. Agent Manager rejects MCP upstreams that resolve to a private address. Run ./deploy/expose-mcp.sh."
fi

# ---- keys: read the ones deploy-mcp.sh generated, fall back to the docker-compose defaults
KEYS="$(kubectl -n sales-demo get secret salesforce-mcp-keys -o jsonpath='{.data.SF_API_KEYS}' 2>/dev/null | base64 --decode 2>/dev/null || true)"
pick() { tr ',' '\n' <<<"$KEYS" | sed -n "s/^$1=//p" | head -1; }
GATEWAY_KEY="${GATEWAY_KEY:-$(pick gateway)}"; GATEWAY_KEY="${GATEWAY_KEY:-sf-gateway-demo-key}"   # attached by the MCP proxy
DIRECT_KEY="${DIRECT_KEY:-$(pick direct)}";    DIRECT_KEY="${DIRECT_KEY:-sf-direct-demo-key}"        # held by the ungoverned agent
echo "repo:      $REPO_URL ($REPO_BRANCH) path $APP_PATH"
echo "MCP proxy: $MCP_PUBLIC_URL"
echo "MCP direct (ungoverned agent): $MCP_DIRECT_URL"
echo "keys:      direct starts ${DIRECT_KEY:0:4}... (held by the ungoverned agent), gateway starts ${GATEWAY_KEY:0:4}... (attached by the MCP proxy)"
[ "$DIRECT_KEY" = "sf-direct-demo-key" ] && echo "  note: these are the built-in demo keys. If the cluster has random keys, re-run with the secret readable by kubectl."

say "Project '$PROJECT'"
soft amctl project create "$PROJECT" --display-name "Sales Copilot Demo" --description "Trusted AI governance demo"
soft amctl context link --project "$PROJECT"

say "LLM provider '$PROVIDER' (the OpenAI key is stored here once, agents never hold it)"
GW_ARGS=(); [ -n "${GATEWAY:-}" ] && GW_ARGS=(--gateways "$GATEWAY")
printf '%s' "$OPENAI_API_KEY" | soft amctl llm-provider create "$PROVIDER" --display-name "Shared OpenAI" \
  --template openai --context /openai --version v1.0 --api-key-stdin ${GW_ARGS[@]+"${GW_ARGS[@]}"}

say "Deploy '$PROVIDER' to the AI gateway (a provider that is not deployed answers 404)"
GATEWAYS_JSON="$(amctl gateway list --env "$ENVIRONMENT" --json 2>/dev/null || true)"
GATEWAY_UUID="${GATEWAY_UUID:-$(jq -r --arg g "${GATEWAY:-}" '
  [(.data.gateways // .gateways // [])[]
   | select(($g == "") or (.name == $g) or (.uuid == $g))
   | select((.gatewayType // "BOTH") | test("EGRESS|BOTH"; "i"))] | first | .uuid // empty' <<<"$GATEWAYS_JSON" 2>/dev/null)}"
if [ -z "$GATEWAY_UUID" ]; then
  echo "  Could not pick a gateway automatically. Run: amctl gateway list --env $ENVIRONMENT"
  echo "  then re-run with GATEWAY=<name>. Or deploy the provider in the Console (LLM Service Providers > Shared OpenAI)."
else
  DEPLOYS="$(amctl api "/orgs/$ORG/llm-providers/$PROVIDER/deployments" 2>/dev/null || true)"
  if [ "$(jq --arg g "$GATEWAY_UUID" '[.. | objects | select((.gatewayId? == $g) and ((.status? // "") | test("^DEPLOYED$"; "i")))] | length' <<<"$DEPLOYS" 2>/dev/null || echo 0)" -gt 0 ]; then
    echo "  already deployed to gateway $GATEWAY_UUID"
  else
    echo "+ deploying to gateway $GATEWAY_UUID"
    jq -nc --arg g "$GATEWAY_UUID" '{name:"demo", base:"current", gatewayId:$g}' \
      | amctl api "/orgs/$ORG/llm-providers/$PROVIDER/deployments" -X POST --input - \
      || echo "  (deployment did not succeed: check the Console, LLM Service Providers > $PROVIDER)"
    echo
  fi
fi

say "Salesforce MCP proxy (OAuth, per-tool authorization)"
ENVS_JSON="$(amctl api "/orgs/$ORG/environments" 2>/dev/null || true)"
ENV_UUID="${ENV_UUID:-$(find_id "$ENVS_JSON" "$ENVIRONMENT" uuid environmentUuid id)}"
[ -n "$ENV_UUID" ] || die "Could not resolve the environment UUID. Run: amctl api /orgs/$ORG/environments   then re-run with ENV_UUID=<uuid>"
echo "environment '$ENVIRONMENT' = $ENV_UUID"

UPSTREAM_AUTH="$(jq -nc --arg k "$GATEWAY_KEY" '{type:"api-key", header:"X-API-Key", value:$k}')"
echo "+ discovering tools at $MCP_PUBLIC_URL"
INFO="$(jq -nc --arg u "$MCP_PUBLIC_URL" --argjson a "$UPSTREAM_AUTH" '{url:$u, auth:$a}' | amctl api "/orgs/$ORG/mcp-proxies/fetch-server-info" -X POST --input -)" \
  || die "Agent Manager could not read the MCP server at $MCP_PUBLIC_URL: $INFO"
# The proxy stores the discovered tool objects as they came back, and scopes may only name tools found here.
TOOLS="$(jq -c '(.tools // .data.tools) // empty' <<<"$INFO")"
TOOL_COUNT="$(jq 'length' <<<"${TOOLS:-[]}")"
EXPECTED="$(jq '[.[]] | add | length' governance/scopes.json)"
[ "${TOOL_COUNT:-0}" -gt 0 ] || die "Discovery returned no tools: $INFO"
[ "$TOOL_COUNT" = "$EXPECTED" ] || echo "  warning: discovered $TOOL_COUNT tools, scopes.json expects $EXPECTED"
echo "  found $TOOL_COUNT tools"

echo "+ POST /orgs/$ORG/mcp-proxies"
PROXY_BODY="$(jq -nc --arg u "$MCP_PUBLIC_URL" --arg e "$ENV_UUID" --argjson a "$UPSTREAM_AUTH" --argjson t "$TOOLS" '{
  id:"salesforce", name:"Salesforce MCP", description:"Mock Salesforce for the trusted AI governance demo",
  version:"v1.0", context:"/salesforce", mcpSpecVersion:"2025-06-18",
  endpoints:[{id:"primary", name:"primary", upstream:{main:{url:$u, auth:$a}}, capabilities:{tools:$t},
              security:{enabled:true, identity:{enabled:true}}, environments:[{environmentUuid:$e}]}]}')"
OUT="$(printf '%s' "$PROXY_BODY" | amctl api "/orgs/$ORG/mcp-proxies" -X POST --input - 2>&1)" || {
  if amctl api "/orgs/$ORG/mcp-proxies/salesforce" >/dev/null 2>&1; then echo "  (proxy 'salesforce' already exists, keeping it)"
  else die "Could not create the MCP proxy: $OUT"; fi
}

say "Scopes: read (own book), team (other reps), write (CRM changes)"
for action in read team write; do
  case $action in
    read)  desc="Read the signed-in account manager's own quota, accounts and pipeline" ;;
    team)  desc="Read other reps' quota, deals, compensation and company-wide account data" ;;
    write) desc="Change CRM records" ;;
  esac
  echo "+ scope salesforce:$action"
  jq -nc --arg a "$action" --arg d "$desc" --slurpfile s governance/scopes.json '{action:$a, description:$d, tools:$s[0][$a]}' \
    | amctl api "/orgs/$ORG/mcp-proxies/salesforce/scopes" -X POST --input - || echo "  (did not succeed, it may already exist; continuing)"
done

say "Roles (per environment)"
ROLES_PATH="/orgs/$ORG/environments/$ENVIRONMENT/agent-identities/roles"
for role in \
  '{"name":"sales-am-assistant","description":"Account manager assistant: own data only","scopes":["salesforce:read"]}' \
  '{"name":"sales-manager-assistant","description":"Sales manager assistant: own data plus team data","scopes":["salesforce:read","salesforce:team"]}'; do
  echo "+ role $(jq -r .name <<<"$role")"
  printf '%s' "$role" | amctl api "$ROLES_PATH" -X POST --input - || echo "  (did not succeed, it may already exist; continuing)"
done

say "Agents (platform-hosted, built from $REPO_URL, path $APP_PATH)"
COMMON=(--subtype chat-api --provisioning internal --repo-url "$REPO_URL" --repo-branch "$REPO_BRANCH" --repo-path "$APP_PATH"
        --build-type buildpack --language python --language-version 3.12 --run-command "python main.py" --env AGENT_VERSION=1.0.0)
echo "-- ungoverned: holds the raw OpenAI key and the shared Salesforce key"
soft amctl agent create "$UNGOVERNED" --display-name "Sales Copilot (ungoverned)" "${COMMON[@]}" \
  --env AGENT_NAME="$UNGOVERNED" --env SF_MCP_URL="$MCP_DIRECT_URL" --env SF_MCP_AUTH=apikey \
  --env-secret OPENAI_API_KEY="$OPENAI_API_KEY" --env-secret SF_MCP_API_KEY="$DIRECT_KEY"
echo "-- governed: no upstream credentials, LLM through the provider, tools through the OAuth MCP proxy"
soft amctl agent create "$GOVERNED" --display-name "Sales Copilot" "${COMMON[@]}" \
  --env AGENT_NAME="$GOVERNED" --env USE_LLM_PROVIDER=true --env SF_MCP_AUTH=agentid \
  --llm-provider "$PROVIDER" --llm-url-env LLM_PROVIDER_URL --llm-api-key-env LLM_PROVIDER_KEY
for a in "$UNGOVERNED" "$GOVERNED"; do
  amctl agent get "$a" >/dev/null 2>&1 || die "Agent '$a' does not exist, so it was not created. Fix the error above and re-run."
done

say "Assign the least-privilege role to the governed agent's AgentID"
AGENT_ID=""
for i in $(seq 1 20); do
  AGENT_ID="$(find_id "$(amctl api "/orgs/$ORG/environments/$ENVIRONMENT/agent-identities/agents" 2>/dev/null || true)" "$GOVERNED" thunderAgentId)"
  [ -n "$AGENT_ID" ] && break
  echo "  waiting for AgentID provisioning ($i/20)..."; sleep 15
done
ROLE_ID="$(find_id "$(amctl api "$ROLES_PATH" 2>/dev/null || true)" sales-am-assistant id roleId)"
if [ -n "$AGENT_ID" ] && [ -n "$ROLE_ID" ]; then
  jq -nc --arg i "$AGENT_ID" '{assignments:[{id:$i, type:"agent"}]}' | amctl api "$ROLES_PATH/$ROLE_ID/assignments/add" -X POST --input -
  echo
else
  echo "  Could not assign automatically (agent id='$AGENT_ID', role id='$ROLE_ID')."
  echo "  Console: Organization > Agent Identities > Roles > sales-am-assistant > add agent '$GOVERNED'."
fi

cat <<MSG

$(printf '\033[1;32m')Scripted part done.$(printf '\033[0m') Finish these in the Console (README step 5):

  1. Guardrails   Organization > LLM Service Providers > Shared OpenAI > Guardrails
                  Prompt Decorator   role=system, content = deploy/governance/prompt-policy.txt
                  Regex Guardrail    invert=true, regex = deploy/governance/injection-regex.txt
  2. Tool config  $GOVERNED > Configure > Tool Configurations > Add > 'Salesforce MCP'
                  URL variable name = SF_MCP_URL, then redeploy the agent
  3. API keys     Each agent > Credentials > Create API Key (copy both)
  4. Demo UI      http://localhost:3000, click the gear icon, paste both chat URLs and keys
MSG
