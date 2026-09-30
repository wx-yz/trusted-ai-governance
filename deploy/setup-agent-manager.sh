#!/usr/bin/env bash
# Configure WSO2 Agent Manager for the Trusted AI Governance demo with amctl.
#
# Creates: a project, the Shared OpenAI LLM provider, the Salesforce MCP proxy (OAuth, per-tool scopes),
# two roles, both agents, and the role assignment for the governed agent.
# Four things are left to the Console on purpose (guardrails, tool configuration, API keys); they are printed at the end.
#
# Requires: amctl (logged in), jq. Re-runnable: steps that already exist just print a message and carry on.
set -uo pipefail
cd "$(dirname "$0")"

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY}"
: "${REPO_URL:?Set REPO_URL to the GitHub repository that contains this demo}"
REPO_BRANCH="${REPO_BRANCH:-main}"
REPO_DIR="${REPO_DIR-trusted-ai-governance}"   # where this folder sits inside the repo ("" if it is the repo root)
ORG="${ORG:-default}"
ENVIRONMENT="${ENVIRONMENT:-default}"
PROJECT="${PROJECT:-sales-demo}"
PROVIDER="${PROVIDER:-shared-openai}"
MCP_UPSTREAM="${MCP_UPSTREAM:-http://salesforce-mcp.sales-demo.svc.cluster.local:8080/mcp}"
GATEWAY_KEY="${GATEWAY_KEY:-sf-gateway-demo-key}"   # the credential the MCP proxy attaches upstream
DIRECT_KEY="${DIRECT_KEY:-sf-direct-demo-key}"      # the shared key the ungoverned agent holds itself
GOVERNED="${GOVERNED:-sales-copilot}"
UNGOVERNED="${UNGOVERNED:-sales-copilot-ungoverned}"
APP_PATH="/${REPO_DIR:+$REPO_DIR/}sales-copilot"

command -v amctl >/dev/null || { echo "amctl not found. Install: curl -fsSL https://wso2.github.io/agent-manager/install.sh | sh"; exit 1; }
command -v jq >/dev/null || { echo "jq is required"; exit 1; }

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
soft() { "$@" || echo "  (did not succeed, it may already exist; continuing)"; }
post() { # post <path> <json>
  echo "+ POST $1"
  printf '%s' "$2" | amctl api "$1" -X POST --input - || echo "  (did not succeed, it may already exist; continuing)"
  echo
}
find_id() { # find_id <json> <name> <field...>: first object called <name> that has one of the fields
  jq -r --arg n "$2" --argjson f "$(printf '%s\n' "${@:3}" | jq -R . | jq -sc .)" \
    '[.. | objects | select((.name? == $n) or (.handle? == $n) or (.agentName? == $n))
      | . as $o | ($f | map($o[.]) | map(select(. != null)) | first)] | map(select(. != null)) | first // empty' <<<"$1"
}

say "Project '$PROJECT'"
soft amctl project create "$PROJECT" --display-name "Sales Copilot Demo" --description "Trusted AI governance demo"
soft amctl context link --project "$PROJECT"

say "LLM provider '$PROVIDER' (the OpenAI key is stored here once, agents never hold it)"
GW_ARGS=(); [ -n "${GATEWAY:-}" ] && GW_ARGS=(--gateways "$GATEWAY")
printf '%s' "$OPENAI_API_KEY" | soft amctl llm-provider create "$PROVIDER" --display-name "Shared OpenAI" \
  --template openai --api-key-stdin ${GW_ARGS[@]+"${GW_ARGS[@]}"}

say "Salesforce MCP proxy (OAuth, per-tool authorization)"
ENVS_JSON="$(amctl api "/orgs/$ORG/environments" 2>/dev/null || true)"
ENV_UUID="${ENV_UUID:-$(find_id "$ENVS_JSON" "$ENVIRONMENT" uuid environmentUuid id)}"
[ -n "$ENV_UUID" ] || { echo "Could not resolve the environment UUID. Run: amctl api /orgs/$ORG/environments   then re-run with ENV_UUID=<uuid>"; exit 1; }
echo "environment '$ENVIRONMENT' = $ENV_UUID"

UPSTREAM_AUTH="$(jq -nc --arg k "$GATEWAY_KEY" '{type:"api-key", header:"X-API-Key", value:$k}')"
post "/orgs/$ORG/mcp-proxies/fetch-server-info" "$(jq -nc --arg u "$MCP_UPSTREAM" --argjson a "$UPSTREAM_AUTH" '{url:$u, auth:$a}')"
# Every tool is listed and covered by a scope below, because a tool without a scope is open to any authenticated caller.
ALL_TOOLS="$(jq -c '[.read[], .team[], .write[]]' governance/scopes.json)"
post "/orgs/$ORG/mcp-proxies" "$(jq -nc --arg u "$MCP_UPSTREAM" --arg e "$ENV_UUID" --argjson a "$UPSTREAM_AUTH" --argjson t "$ALL_TOOLS" '{
  id:"salesforce", name:"Salesforce MCP", description:"Mock Salesforce for the trusted AI governance demo",
  version:"v1.0", context:"/salesforce", mcpSpecVersion:"2025-06-18",
  endpoints:[{id:"primary", name:"primary", upstream:{main:{url:$u, auth:$a}}, capabilities:{tools:$t},
              security:{enabled:true, identity:{enabled:true}}, environments:[{environmentUuid:$e}]}]}')"

say "Scopes: read (own book), team (other reps), write (CRM changes)"
for action in read team write; do
  case $action in
    read)  desc="Read the signed-in account manager's own quota, accounts and pipeline" ;;
    team)  desc="Read other reps' quota, deals, compensation and company-wide account data" ;;
    write) desc="Change CRM records" ;;
  esac
  post "/orgs/$ORG/mcp-proxies/salesforce/scopes" \
    "$(jq -nc --arg a "$action" --arg d "$desc" --slurpfile s governance/scopes.json '{action:$a, description:$d, tools:$s[0][$a]}')"
done

say "Roles (per environment)"
ROLES_PATH="/orgs/$ORG/environments/$ENVIRONMENT/agent-identities/roles"
post "$ROLES_PATH" '{"name":"sales-am-assistant","description":"Account manager assistant: own data only","scopes":["salesforce:read"]}'
post "$ROLES_PATH" '{"name":"sales-manager-assistant","description":"Sales manager assistant: own data plus team data","scopes":["salesforce:read","salesforce:team"]}'

say "Agents (platform-hosted, built from $REPO_URL $APP_PATH)"
COMMON=(--subtype chat-api --provisioning internal --repo-url "$REPO_URL" --repo-branch "$REPO_BRANCH" --repo-path "$APP_PATH"
        --build-type buildpack --language python --language-version 3.12 --run-command "python main.py" --env AGENT_VERSION=1.0.0)
echo "-- ungoverned: holds the raw OpenAI key and the shared Salesforce key"
soft amctl agent create "$UNGOVERNED" --display-name "Sales Copilot (ungoverned)" "${COMMON[@]}" \
  --env AGENT_NAME="$UNGOVERNED" --env SF_MCP_URL="$MCP_UPSTREAM" --env SF_MCP_AUTH=apikey \
  --env-secret OPENAI_API_KEY="$OPENAI_API_KEY" --env-secret SF_MCP_API_KEY="$DIRECT_KEY"
echo "-- governed: no upstream credentials, LLM through the provider, tools through the OAuth MCP proxy"
soft amctl agent create "$GOVERNED" --display-name "Sales Copilot" "${COMMON[@]}" \
  --env AGENT_NAME="$GOVERNED" --env USE_LLM_PROVIDER=true --env SF_MCP_AUTH=agentid \
  --llm-provider "$PROVIDER" --llm-url-env LLM_PROVIDER_URL --llm-api-key-env LLM_PROVIDER_KEY

say "Assign the least-privilege role to the governed agent's AgentID"
AGENT_ID=""
for i in $(seq 1 20); do
  AGENT_ID="$(find_id "$(amctl api "/orgs/$ORG/environments/$ENVIRONMENT/agent-identities/agents" 2>/dev/null || true)" "$GOVERNED" thunderAgentId)"
  [ -n "$AGENT_ID" ] && break
  echo "  waiting for AgentID provisioning ($i/20)..."; sleep 15
done
ROLE_ID="$(find_id "$(amctl api "$ROLES_PATH" 2>/dev/null || true)" sales-am-assistant id roleId)"
if [ -n "$AGENT_ID" ] && [ -n "$ROLE_ID" ]; then
  post "$ROLES_PATH/$ROLE_ID/assignments/add" "$(jq -nc --arg i "$AGENT_ID" '{assignments:[{id:$i, type:"agent"}]}')"
else
  echo "  Could not assign automatically (agent id='$AGENT_ID', role id='$ROLE_ID')."
  echo "  Console: Organization > Agent Identities > Roles > sales-am-assistant > add agent '$GOVERNED'."
fi

cat <<MSG

$(printf '\033[1;32m')Scripted part done.$(printf '\033[0m') Finish these in the Console (see README step 4):

  1. Guardrails   Organization > LLM Service Providers > Shared OpenAI > Guardrails
                  Prompt Decorator   role=system, content = governance/prompt-policy.txt
                  Regex Guardrail    invert=true, regex = governance/injection-regex.txt
  2. Tool config  $GOVERNED > Configure > Tool Configurations > Add > 'Salesforce MCP'
                  URL variable name = SF_MCP_URL, then redeploy the agent
  3. API keys     Each agent > Credentials > Create API Key (copy both)
  4. Console      Open the demo UI, click the gear icon, paste both chat URLs and keys
MSG
