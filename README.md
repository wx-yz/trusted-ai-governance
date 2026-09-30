# Trusted AI Governance: 5-minute demo

An AI assistant for account managers, hosted on **WSO2 Agent Manager**, reads Salesforce through an **MCP server**.
Run the same assistant twice, ungoverned and governed, and watch the same questions **leak** another rep's
compensation in one and get **blocked** in the other. A live dashboard shows every leak and every block.

![Governance ON: the same risky prompts, blocked at the gateway](docs/chat-and-dashboard.png)

```
                          ┌────────────────────────────── WSO2 Agent Manager ──────────────────────────────┐
 Account manager          │                                                                                │
 (chat UI, browser) ─────►│  sales-copilot-ungoverned ── OpenAI key + shared Salesforce key ──► OpenAI     │
        │                 │                           └──────────────────────────────────────► Salesforce │
        │                 │                                                                     MCP server │
        │                 │  sales-copilot (governed) ─► AI gateway: guardrails ─► OpenAI      (mock data, │
        │                 │                           └► MCP proxy: AgentID + tool scopes ──►  audit log)  │
        ▼                 └────────────────────────────────────────────────────────────────────────────────┘
 Dashboard (same page): reads the MCP audit log + what each agent reports the gateways refused (403 / 422)
```

| Folder | What it is |
|---|---|
| `salesforce-mcp/` | Real MCP server (streamable HTTP) over fictional Salesforce data. 13 tools in 3 scopes, plus an audit log of whose data each call returned. |
| `sales-copilot/` | The chat agent (LangGraph, `POST /chat`). One codebase, deployed **twice** with different configuration. |
| `governance-console/` | Chat UI with a **Governance ON/OFF** switch and the live dashboard. Static site, no backend. |
| `deploy/` | Kubernetes manifests, `deploy-mcp.sh`, `setup-agent-manager.sh` (amctl), guardrail values. |
| `DEMO-SCRIPT.md` | The 5-minute talk track. |
| `tests/` | `python tests/test_demo.py` runs every scenario locally with a scripted LLM. |

## The two deployments (same code)

| | `sales-copilot-ungoverned` | `sales-copilot` (governed) |
|---|---|---|
| LLM | Holds the raw OpenAI key | Through the `Shared OpenAI` provider. Guardrails enforced at the AI gateway. Holds no key. |
| Salesforce | Holds a shared integration key, sees all 13 tools | OAuth MCP proxy. Holds no key. Its **AgentID** carries only `salesforce:read`. |
| Result | Leaks other reps' data, follows prompt injection, edits the CRM | Reads own data. Everything else is stopped outside the agent. |

## Prerequisites

- Agent Manager running ([Quick Start](https://wso2.com/agent-platform/docs/v1.0.0/get-started/quick-start/), k3d cluster `amp-local`), logged in as `admin`.
- `docker`, `kubectl`, `k3d`, `jq`, and [`amctl`](https://wso2.github.io/agent-manager/install.sh) logged in: `amctl login --url http://api.amp.localhost:8080`
- An OpenAI API key.
- A GitHub repository Agent Manager can read (public, or private plus a Git secret).

## Deploy

**1. Push to GitHub.** Push this folder to a repository. Agent Manager builds the agents from `sales-copilot/`.

**2. Run the Salesforce MCP server and the console in the cluster.**
```bash
./deploy/deploy-mcp.sh
kubectl -n sales-demo port-forward svc/salesforce-mcp 8090:8080 &        # dashboard reads the audit log here
kubectl -n sales-demo port-forward svc/governance-console 3000:80 &      # the demo UI
```
No `k3d` CLI? Run `docker compose up -d --build` instead and set `MCP_UPSTREAM=http://host.k3d.internal:8090/mcp` in step 3.

**3. Configure Agent Manager** (project, `Shared OpenAI` provider, Salesforce MCP proxy with OAuth and scopes, two roles, both agents, role assignment):
```bash
export OPENAI_API_KEY=sk-...
export REPO_URL=https://github.com/<you>/<repo>.git   # REPO_DIR=<folder in repo>, default trusted-ai-governance
./deploy/setup-agent-manager.sh
```
Prefer clicking? Every object it creates is an ordinary Console form: see the values in the script, and the Agent Manager guides
*Register an MCP Proxy*, *Authorize Agent Access to MCP Tools* and *Add guardrails to your agent*.

**4. Finish in the Console** (the four things the script leaves to you):

1. **Guardrails** – Organization, *LLM Service Providers*, `Shared OpenAI`, *Guardrails*, *Add Guardrail*, then **Save**:
   - *Prompt Decorator*: messages, add item, role `system`, content from `deploy/governance/prompt-policy.txt`.
   - *Regex Guardrail*: request regex from `deploy/governance/injection-regex.txt`, **Invert on**, leave the JSONPath default, leave Response off.
2. **Salesforce tool for the governed agent** – `sales-copilot`, *Configure*, *Tool Configurations*, *Add*, `Salesforce MCP`; set the URL variable name to `SF_MCP_URL`; redeploy.
3. **API keys** – each agent, *Credentials*, *Create API Key*. Copy the two keys and the two invoke URLs (they end in `/chat`).
4. **Demo UI** – open <http://localhost:3000>, click ⚙, paste both URLs and keys.

**5. Smoke test.** Governance OFF: click *How am I pacing against my quota?* (works), then the Jordan Lee prompt (red **DATA LEAK**).
Governance ON: the same prompts give violet **BLOCKED · AgentID** and amber **BLOCKED · Guardrail** cards.
Then follow `DEMO-SCRIPT.md`. The ↺ button resets data, chat and dashboard between runs.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Chat says the agent is not configured | Governed agent: finish step 4.2 and redeploy, and wait for AgentID to provision. Check `GET /health` or the agent logs. |
| Dashboard shows *audit feed offline* | The `8090` port-forward is not running, or ⚙ audit URL is wrong. |
| Browser cannot reach an agent | Use the invoke URL ending in `/chat`, the API key from *Credentials*, and leave CORS on (default). |
| Own-data calls also return 403 | The role lacks `salesforce:read`, or the agent was created before the scopes existed. Regenerate its AgentID credential (agent, *Agent ID*) and restart it. |
| Governed agent still leaks | The `sales-am-assistant` role is not assigned to it, or it holds a wider role. Fix and restart the agent, tokens are cached for their lifetime. |
| `get_my_*` fails with *No signed-in account manager* | The gateway dropped the `X-Acting-User` header. The MCP audit shows `acting_user: null`. |
| No red/amber cards for injection | The Regex Guardrail is missing or Invert is off. Check the provider's *Guardrails* tab. |
| Agent cannot reach the MCP server directly | A network policy blocks the namespace. Use `host.k3d.internal:8090` with `docker compose`. |

Clean up: `kubectl delete ns sales-demo`, then delete the two agents, the `shared-openai` provider, the `salesforce` MCP proxy and the roles in the Console.

## Notes

- Nothing here was executed against a live Agent Manager cluster. The MCP server, both agents, the console and the dashboard were run end to end locally against stand-ins for the gateway behaviour (`tests/fakes.py`); the Console steps and `amctl` calls follow the published docs.
- The signed-in user travels as an `X-Acting-User` header from the demo UI. A real deployment forwards a signed user token instead.
- Demo secrets (`sf-direct-demo-key`, `sf-gateway-demo-key`) are placeholders. The `/audit` endpoint is open on purpose. Do not expose either outside a demo.
