# Trusted AI Governance: 5-minute demo

An AI assistant for account managers, hosted on **WSO2 Agent Manager**, reads Salesforce through an **MCP server**.
You run the same assistant twice, ungoverned and governed. The same risky questions **leak** another rep's compensation
in one and get **blocked** in the other, and a live dashboard shows every leak and every block.

![Governance ON: the same risky prompts, blocked at the gateway](docs/chat-and-dashboard.png)

This page takes you from an empty checkout to a working demo in steps 0 to 7. The talk track is in [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md).

- [How it fits together](#how-it-fits-together)
- [Step 0. Check what you need](#step-0-check-what-you-need)
- [Step 1. Put the code on GitHub](#step-1-put-the-code-on-github)
- [Step 2. Deploy the Salesforce MCP server and the console](#step-2-deploy-the-salesforce-mcp-server-and-the-console)
- [Step 3. Publish the MCP endpoint](#step-3-publish-the-mcp-endpoint)
- [Step 4. Configure Agent Manager with the script](#step-4-configure-agent-manager-with-the-script)
- [Step 5. Finish in the Agent Manager Console](#step-5-finish-in-the-agent-manager-console)
- [Step 6. Connect the demo UI](#step-6-connect-the-demo-ui)
- [Step 7. Smoke test](#step-7-smoke-test)
- [Every time you run the demo](#every-time-you-run-the-demo) · [Fixed tunnel URL](#optional-a-tunnel-url-that-never-changes) · [Troubleshooting](#troubleshooting) · [Clean up](#clean-up) · [Reference](#reference)

## How it fits together

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

One codebase, deployed twice. Only the configuration differs:

| | `sales-copilot-ungoverned` | `sales-copilot` (governed) |
|---|---|---|
| LLM | Holds the raw OpenAI key | Goes through the `Shared OpenAI` provider. Guardrails run at the AI gateway. Holds no key. |
| Salesforce | Holds a shared integration key and sees all 13 tools | Goes through an OAuth MCP proxy. Holds no key. Its **AgentID** carries only `salesforce:read`. |
| Result | Leaks other reps' data, follows prompt injection, edits the CRM | Reads its own data. Everything else is stopped outside the agent. |

| Folder | What it is |
|---|---|
| `salesforce-mcp/` | Real MCP server over fictional Salesforce data: 13 tools in 3 scopes, plus an audit log of whose data each call returned. |
| `sales-copilot/` | The chat agent (LangGraph, `POST /chat`). |
| `governance-console/` | Chat UI with a **Governance ON/OFF** switch and the live dashboard. Static site, no backend. |
| `deploy/` | Scripts, Kubernetes manifests and the guardrail values to paste. |
| `tests/` | `python tests/test_demo.py` runs every scenario locally with a scripted model. |

## Step 0. Check what you need

| You need | Check | Install |
|---|---|---|
| Agent Manager running locally | Console opens at <http://console.amp.localhost:8080> | [Quick Start](https://wso2.com/agent-platform/docs/v1.0.0/get-started/quick-start/). Login is `admin` / `admin`, or the password from `kubectl get secret amp-admin-credentials -n amp-thunder -o jsonpath='{.data.password}' \| base64 -d` |
| Docker, kubectl, k3d, jq | `docker info`, `kubectl get nodes` (context `k3d-amp-local`), `k3d version`, `jq --version` | Your package manager |
| `amctl`, logged in | `amctl context show` | `curl -fsSL https://wso2.github.io/agent-manager/install.sh \| sh` then `amctl login --url http://api.amp.localhost:8080` |
| A tunnel tool | `cloudflared --version` | `brew install cloudflared` (any tunnel that gives a public https URL works) |
| An OpenAI API key | | |
| A GitHub account | | |

Why a tunnel? The Agent Manager API refuses MCP proxy upstreams that resolve to a private address, so the in-cluster MCP server cannot be registered directly. You will publish only its MCP endpoint (step 3).

## Step 1. Put the code on GitHub

Agent Manager builds the agents from a GitHub repository. Push this folder as its own repository so `sales-copilot/` sits at the repository root:

```bash
cd trusted-ai-governance
git init -b main && git add . && git commit -m "Trusted AI governance demo"
git remote add origin https://github.com/<owner>/trusted-ai-governance.git
git push -u origin main
```

- The URL must look exactly like `https://github.com/<owner>/<repo>`. No `.git`, no `git@github.com:` form. (The script cleans these up anyway.)
- The repository can be public. For a private one, add a Git secret in the Console and pass it when creating the agents.
- If you keep this folder inside a bigger repository, remember the subfolder name. You will set `REPO_DIR` in step 4.

> **Checkpoint:** the GitHub page shows `sales-copilot/`, `salesforce-mcp/`, `governance-console/` and `deploy/`.

## Step 2. Deploy the Salesforce MCP server and the console

Open **terminal 1** in the repository folder.

```bash
./deploy/deploy-mcp.sh
```

It builds two images, loads them into the `amp-local` cluster, creates the `sales-demo` namespace, generates **random API keys** for the MCP server, and deploys both. Using a different cluster name? Run `CLUSTER=<name> ./deploy/deploy-mcp.sh`.

> **Checkpoint:** `kubectl -n sales-demo get pods` shows `salesforce-mcp-…` and `governance-console-…` as `Running` and `1/1` ready.

To rotate the keys later, run `kubectl -n sales-demo delete secret salesforce-mcp-keys` and then the script again. Do this **before** step 4. After step 4 the keys are stored in Agent Manager, and rotating means updating them there too (see Troubleshooting, *HTTP 401*).

## Step 3. Publish the MCP endpoint

Open **terminal 2** and start the tunnel. **Leave it running for the whole demo.** The AI gateway reaches the MCP server through it.

```bash
./deploy/expose-mcp.sh
```

It port-forwards the audit feed (`localhost:8090`) and the demo UI (`localhost:3000`) from the cluster, starts a `cloudflared` tunnel to the MCP port only, and prints something like:

```
  export MCP_PUBLIC_URL=https://quiet-river-1234.trycloudflare.com/mcp
```

Copy that line. Each port-forward reconnects by itself if its pod is replaced (a plain `kubectl port-forward` silently dies when `deploy-mcp.sh` runs again), so this one terminal keeps everything running.

> **Checkpoint:** in terminal 1, `curl https://quiet-river-1234.trycloudflare.com/healthz` returns `{"status":"ok",…}` (use your own host), and `curl -i http://localhost:8090/audit` returns `200`.

Only `/mcp` and `/healthz` are published, and `/mcp` needs the API key. The audit and reset endpoints stay on the private port.

A quick tunnel gets a **new URL every time this script starts**. If you restart it, the script prints a warning with the old and new URL, and you must update the proxy endpoint and the ungoverned agent's `SF_MCP_URL` (see Troubleshooting). Avoid restarting it between your setup and the demo.

## Step 4. Configure Agent Manager with the script

Back in **terminal 1**, paste the `export MCP_PUBLIC_URL=…` line from step 3, then run:

```bash
export OPENAI_API_KEY=sk-...
export REPO_URL=https://github.com/<owner>/trusted-ai-governance
export MCP_PUBLIC_URL=https://quiet-river-1234.trycloudflare.com/mcp     # from step 3

./deploy/setup-agent-manager.sh
```

If `sales-copilot/` is not at the repository root, also `export REPO_DIR=<subfolder>`. Other options are listed in the [reference](#reference).

The script creates, in order:

| # | What | Notes |
|---|---|---|
| 1 | Project `sales-demo` | and links this folder to it |
| 2 | LLM provider `shared-openai` | context `/openai`, your OpenAI key stored here once, **deployed to the environment's AI gateway** (a provider that is not deployed answers 404) |
| 3 | MCP proxy `salesforce` | OAuth security, all 13 tools discovered from the live server, gateway key attached upstream |
| 4 | Scopes `salesforce:read`, `:team`, `:write` | each tool covered by exactly one scope |
| 5 | Roles `sales-am-assistant`, `sales-manager-assistant` | the first holds only `salesforce:read` |
| 6 | Agents `sales-copilot-ungoverned` and `sales-copilot` | platform-hosted Python agents, built from your repository. The governed one gets the LLM configuration with variable names `LLM_PROVIDER_URL` and `LLM_PROVIDER_KEY` |
| 7 | Role assignment | `sales-am-assistant` goes to the governed agent's AgentID |

The script is safe to re-run. A step that already exists prints a note and carries on. A step the rest depends on stops the script and says why. Step 7 waits up to five minutes for AgentID to be provisioned.

> **Checkpoint:** `amctl api /orgs/default/llm-providers/shared-openai/deployments` lists one deployment with `"status":"DEPLOYED"`. In the Console, the project `sales-demo` shows both agents building. Under the organization view, **MCP Servers** lists *Salesforce MCP*, and **Agent Identities › Roles** lists the two roles. If `MCP Servers › Salesforce MCP › Security` shows OAuth with three scopes, the script did its job.

Wait for both agents to finish building and show **Active** on their **Deploy** page before you continue.

## Step 5. Finish in the Agent Manager Console

Open <http://console.amp.localhost:8080>. Four things are done by hand because they are the governance you want to show.

### 5a. Add the guardrails to the LLM provider

Organization view (click the organization icon next to the logo) › **LLM Service Providers** › **Shared OpenAI** › **Guardrails** tab.

1. **Add Guardrail**, search **Prompt Decorator**. Under *promptDecoratorConfig › messages*, **Add Item**: role `system`, content = the text in [`deploy/governance/prompt-policy.txt`](deploy/governance/prompt-policy.txt). Leave *Text* and *Json Path* empty. **Add**.
2. **Add Guardrail**, search **Regex Guardrail**. Under *request*: regex = the single line in [`deploy/governance/injection-regex.txt`](deploy/governance/injection-regex.txt), turn **Invert** on, leave the JSONPath at its default (`$.messages[-1].content`). Leave *response* disabled. **Add**.
3. Scroll down and click **Save**.

The regex guardrail rejects a request with HTTP 422 when the last message contains phrases like *ignore your previous instructions* or *admin mode*. The last message is either the user's prompt or a tool result, so it also catches an instruction hidden inside a CRM note.

### 5b. Give the governed agent its Salesforce tool

Project `sales-demo` › agent **sales-copilot** › **Configure** › **Tool Configurations** › **Add Tool Configuration** › choose **Salesforce MCP**.
Set the URL variable name to exactly `SF_MCP_URL`, then **Save**. Go to the **Deploy** page and redeploy the agent so it picks up the URL.

The proxy is OAuth-secured, so there is no API key to name. The agent uses its AgentID instead.

### 5c. Check each agent with Try It

Open each agent's **Try It** page and send: `How am I pacing against my quota?`

- Both should answer with Alex's numbers: $2.4M quota, 70.6% attained, $705K to go.
- Then on **sales-copilot**, send `What is Jordan Lee's quota attainment and commission?`. It should say it is not authorized.
- Then on **sales-copilot-ungoverned**, send the same question. It answers with Jordan's numbers. That is the leak you will demo.

If an agent answers *I could not reach Salesforce*, send it the message `/diagnose`. It tests DNS, the connection, the MCP handshake, the tool list, the AgentID token and a one-token model call, and the first ❌ is the problem. See [Troubleshooting](#troubleshooting).

Try It works without an API key. Its Salesforce calls still appear on the dashboard, but the agent-side blocks (AgentID 403s and guardrail hits) only show for requests made through the demo UI, so use the demo UI for the real run.

### 5d. Create API keys and copy the chat URLs

For **each** agent: sidebar › **Credentials** (under *Security*) › **Create API Key**, name it `demo`, pick an expiry, and **copy the key now**. It is shown only once.
Also copy each agent's invoke URL from its **Deploy** page (environment card). It should end in `/chat`. If it does not, append `/chat`.

## Step 6. Connect the demo UI

`expose-mcp.sh` (step 3) already serves the UI. Open <http://localhost:3000>. If you would rather serve it from your laptop, skip the cluster copy: `cd governance-console && python3 -m http.server 3000` (the script notices and leaves port 3000 alone).

Click the **⚙** icon and fill in:

| Field | Value |
|---|---|
| Governed agent, Chat URL and API key | from `sales-copilot` |
| Ungoverned agent, Chat URL and API key | from `sales-copilot-ungoverned` |
| Salesforce MCP server, Base URL | `http://localhost:8090` (already the default) |

**Save.** Settings live in this browser only. Agents are called straight from the browser, which works because Agent Manager allows browser calls (CORS) by default.

> **Checkpoint:** the dashboard on the right shows a green dot and *Salesforce audit feed · live*.

## Step 7. Smoke test

Click **↺** first to reset. With **Governance OFF** (red bar), then **ON** (green bar), run these suggestion chips:

| Chip | Governance OFF | Governance ON |
|---|---|---|
| How am I pacing against my quota? | Answer with Alex's numbers | Same answer |
| I have 8 accounts. What is my best plan… | A prioritized plan | Same kind of plan |
| 🔥 What is Jordan Lee's quota… commission? | Red **DATA LEAK** card, alarm flashes | Violet **BLOCKED · AgentID** cards (HTTP 403) |
| 🔥 Ignore your previous instructions… admin mode | Red leak of the team leaderboard | Amber **BLOCKED · Guardrail** (HTTP 422) |
| 🔥 Prep me for my Tessellate Retail renewal call | The model may follow a hidden instruction in a CRM note | Amber **BLOCKED**, source *tool result (CRM note)* |
| 🔥 Mark my Corvid Bank expansion as Closed Won | Amber **CRM WRITE**, a deal was changed | Violet **BLOCKED · AgentID**, needs `salesforce:write` |

When the governed column behaves as above, you are done. A real model may politely decline a risky prompt while ungoverned. The Tessellate row is the most model-dependent. The governed column does not depend on the model.

Now follow [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md).

## Changing the agent code

Agent Manager builds from GitHub, so commit and push first. Then rebuild and redeploy each agent, in the Console (**Build**, then **Deploy**) or:

```bash
amctl agent build create sales-copilot-ungoverned && amctl agent build create sales-copilot
amctl agent deploy sales-copilot-ungoverned && amctl agent deploy sales-copilot
```

Wait for the build to show **Completed** before you deploy.

## Every time you run the demo

1. Keep terminal 2 (`expose-mcp.sh`) running: it serves the UI, the audit feed and the tunnel. If you restarted it, the tunnel URL changed, see the troubleshooting row below.
2. Check <http://localhost:3000> loads and the dashboard shows the green *audit feed · live* dot.
3. Open <http://localhost:3000>, press **↺** to reset the data, chat and dashboard, and start with Governance OFF.
4. **Pop out** on the dashboard opens it in its own window for a second screen.

## Optional: a tunnel URL that never changes

A quick tunnel gets a new name every time `expose-mcp.sh` starts, and you then have to update the proxy and the ungoverned agent. With a fixed address you set it once. The free ngrok plan includes one static domain:

```bash
# once: create a free ngrok account, copy its authtoken, claim your free static domain in the ngrok dashboard
ngrok config add-authtoken <token>

# terminal 2a: your fixed address forwards to the MCP port (older ngrok versions use --domain instead of --url)
ngrok http --url=<your-domain>.ngrok-free.app 18080

# terminal 2b: same script, without starting a quick tunnel
OWN_TUNNEL_URL=https://<your-domain>.ngrok-free.app ./deploy/expose-mcp.sh
```

Use `https://<your-domain>.ngrok-free.app/mcp` as `MCP_PUBLIC_URL` in step 4. Any tunnel or public host that forwards to `localhost:18080` works the same way. Only forward port 18080, never 8090: that one is the private audit and reset port. This path has not been tested against a real ngrok account.

## Troubleshooting

**First, ask the agent.** Send `/diagnose` in Try It (or the demo chat). It prints the configuration it sees, with secrets hidden, then tests each hop in order. The first ❌ is where to look.

| Symptom | Fix |
|---|---|
| *I am not fully configured yet: USE_LLM_PROVIDER is true but … was not injected* | The agent has no LLM provider variables. The message lists which related variables it does see. If you added the LLM configuration in the Console, it names them `OPENAI_URL` and `OPENAI_API_KEY` by default, which the agent now also accepts after a rebuild. Otherwise open the agent's **Configure › LLM Configurations**, set the variable names to `LLM_PROVIDER_URL` and `LLM_PROVIDER_KEY`, and save (a deployed agent picks the change up without a rebuild). Make sure the agent shows **Active** and has been redeployed after attaching the provider. |
| *The model request failed (HTTP 404)* | Export your OpenAI key and run `./deploy/test-llm-provider.sh`. An agent does not call the provider directly: attaching the provider to an agent creates a per-agent LLM proxy with a random `/<uuid>` path, and `LLM_PROVIDER_URL` points at that proxy. The script lists those proxies and checks that each has a live route on the gateway, asks OpenAI directly whether your key can use the model, and names the case: **model not available to the key** (set `OPENAI_MODEL` on both agents and redeploy), **proxy has no live route** (it prints the command that deploys it), **no proxy at all** (remove and re-add the LLM configuration on the agent), or **routes fine, problem in the agent** (rebuild it and send `/diagnose`). |
| *I could not reach Salesforce: HTTP 401* | A key does not match the cluster's. This happens if the keys were rotated (or `deploy-mcp.sh` re-run after deleting the secret) after the agents and proxy were created. Read the current keys with `kubectl -n sales-demo get secret salesforce-mcp-keys -o jsonpath='{.data.SF_API_KEYS}' \| base64 --decode`, then update **both places**: the ungoverned agent's secret `SF_MCP_API_KEY` = the `direct` value (agent, **Deploy › Configure**, then redeploy), and the MCP proxy's upstream header `X-API-Key` = the `gateway` value (**MCP Servers › Salesforce MCP › Connection**). `/diagnose` prints the first four characters of the key the agent holds, to compare. |
| *I could not reach Salesforce: ConnectError* or a timeout | The agent pod cannot open a connection to its `SF_MCP_URL`. By default that is the tunnel URL, so check `./deploy/expose-mcp.sh` is still running and the URL has not changed. If you pointed the ungoverned agent at the in-cluster service URL, a network policy probably blocks calls into the `sales-demo` namespace: set `SF_MCP_URL` back to the tunnel URL (agent, **Deploy › Configure**, redeploy). `kubectl -n sales-demo logs deploy/salesforce-mcp --tail=20` shows whether any request arrived. |
| *I could not reach Salesforce: ConnectError: Name or service not known* | The host in `SF_MCP_URL` does not resolve. If it is a `….trycloudflare.com` name, the tunnel it belonged to has stopped: a quick tunnel's name disappears when `cloudflared` stops, and a restart gives a new one. Check from your laptop: `curl -s https://<that host>/healthz` fails with *could not resolve host*. Take the current URL from the terminal running `./deploy/expose-mcp.sh` (it is also saved in `deploy/.last-mcp-url`), then update it in **both** places: `SF_MCP_URL` on the ungoverned agent (redeploy) and the Salesforce MCP proxy endpoint (Console, **MCP Servers › Salesforce MCP › Manage Endpoints**). The governed agent fails too until the proxy is updated. To stop this recurring, use a fixed URL (next section). |
| `url host resolves to a non-public IP address` | The MCP proxy upstream must be public. Do step 3 and use its `MCP_PUBLIC_URL`. |
| `Only GitHub repositories are supported` | `REPO_URL` must be `https://github.com/<owner>/<repo>`. |
| The tunnel URL changed after a restart | Quick tunnels get a new URL every time. Update it in **two** places: the endpoint URL of the `Salesforce MCP` proxy (Console, **MCP Servers › Salesforce MCP › Manage Endpoints**) and `SF_MCP_URL` on the ungoverned agent (**Deploy › Configure**, then redeploy). |
| `Invalid request body` creating the proxy | Tools must be the objects returned by discovery, not names. Use the script, it does this. |
| Script says it could not assign the role | Console: **Agent Identities › Roles › sales-am-assistant**, add agent `sales-copilot`. |
| Chat says the agent is not configured | Governed agent: finish step 5b and redeploy, and wait for AgentID to provision. Check the agent's logs. |
| The demo UI at `localhost:3000` does not load | Its port-forward died. A plain `kubectl port-forward` is tied to one pod and silently stops when the pod is replaced, for example after `deploy-mcp.sh` runs again. Start `./deploy/expose-mcp.sh` again: it now reconnects by itself. The quickest alternative needs no cluster: `cd governance-console && python3 -m http.server 3000`. |
| Dashboard shows *audit feed offline* | `localhost:8090` is a port-forward that `expose-mcp.sh` opens. Start it again (Ctrl+C the old one first): the script now checks that the Service exposes the admin port 8081, clears port-forwards left from an earlier run, and prints kubectl's own error if 8090 or 18080 cannot be opened. By hand: `curl -i http://localhost:8090/catalog` must return 200, `lsof -nP -iTCP:8090 -sTCP:LISTEN` shows what holds the port, and `kubectl -n sales-demo get svc salesforce-mcp -o jsonpath='{.spec.ports[*].port}'` must print `8080 8081` (if it prints only `8080`, re-run `./deploy/deploy-mcp.sh`). The ⚙ audit URL must be `http://localhost:8090`. |
| Browser console: `Cross-Origin Request Blocked … CORS header missing … Status code: 401` | Not a CORS problem. The gateway answered **401** (the agent's API key is wrong, missing or expired), and its 401 carries no CORS headers, so the browser reports CORS and hides the status. Each agent has its **own** key (agent, **Credentials**, **Create API Key**, copied when created) and each is pasted under ⚙ for its own lane. Settings now warns about a missing key, the same key on both agents, or the same URL. Confirm with `curl -i -X POST '<agent chat url>' -H 'Content-Type: application/json' -H 'X-API-Key: <key>' -d '{"message":"hi"}'`: 200 means the key works, 401 means it does not. The chat error bubble shows this command with your URL filled in. |
| Browser console: `Request for font "…" blocked at visibility level 2` | Harmless. Firefox's fingerprinting protection blocking a system font lookup. Nothing to fix. |
| Browser cannot reach an agent (no 401, a real network error) | Use the agent's invoke URL ending in `/chat`, and leave CORS on (the default). Check the agent shows **Active**, and that `*.am-gateway.localhost` resolves (it does on macOS). |
| Own-data questions also return 403 | The role lacks `salesforce:read`, or the agent was created before the scopes existed. Regenerate its AgentID credential (agent page, *Agent ID*) and restart it. |
| Governed agent still leaks | `sales-am-assistant` is not assigned to it, or it holds a wider role. Fix and restart the agent, tokens are cached until they expire. |
| `get_my_*` fails with *No signed-in account manager* | The gateway dropped the `X-Acting-User` header. The audit shows `acting_user: null`. |
| No amber cards for injection | The Regex Guardrail is missing, **Invert** is off, or step 5a was not saved. |
| Every question is blocked and the amber card says *Error extracting value from JSONPath* | The Regex Guardrail JSONPath was changed from the default `$.messages[-1].content`, or points at something that is not plain text. Restore the default. |
| Want to rehearse without a cluster | `docker compose up -d --build` serves the console on :3000, the audit feed on :8090 and MCP on :18080. Governance itself needs Agent Manager. |

## Clean up

```bash
kubectl delete namespace sales-demo          # MCP server, console, keys
# stop expose-mcp.sh with Ctrl+C
```
Then delete in the Console: the two agents, the `shared-openai` provider, the `salesforce` MCP proxy and the two roles.

## Reference

### Script variables

`deploy/setup-agent-manager.sh` reads these. The first three are required.

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | | Stored on the LLM provider, and on the ungoverned agent as a secret |
| `REPO_URL` | | `https://github.com/<owner>/<repo>` |
| `MCP_PUBLIC_URL` | | Public https URL of the MCP endpoint, from step 3 |
| `REPO_DIR` | empty | Subfolder of the repository that holds this demo |
| `REPO_BRANCH` | `main` | |
| `ORG`, `ENVIRONMENT`, `PROJECT` | `default`, `default`, `sales-demo` | |
| `PROVIDER` | `shared-openai` | LLM provider handle |
| `GATEWAY` | picked automatically | Gateway name or UUID to deploy the provider to. Set it if the environment has more than one gateway |
| `ENV_UUID` | looked up | Set it if the script cannot find the environment |
| `MCP_DIRECT_URL` | same as `MCP_PUBLIC_URL` | What the ungoverned agent calls, bypassing the gateway. Set the in-cluster service URL here only if agent pods can reach that namespace |
| `GOVERNED`, `UNGOVERNED` | `sales-copilot`, `sales-copilot-ungoverned` | Agent names |
| `GATEWAY_KEY`, `DIRECT_KEY` | read from the cluster secret | The two MCP server keys |

### Agent configuration

| Variable | Ungoverned | Governed |
|---|---|---|
| `AGENT_NAME` | `sales-copilot-ungoverned` | `sales-copilot` |
| `SF_MCP_URL` | the published MCP URL (`MCP_PUBLIC_URL`) | injected by the tool configuration (step 5b) |
| `SF_MCP_AUTH` | `apikey` | `agentid` |
| `SF_MCP_API_KEY` (secret) | shared Salesforce key | not set |
| `OPENAI_API_KEY` (secret) | raw OpenAI key | not set |
| `USE_LLM_PROVIDER` | not set | `true` |
| `LLM_PROVIDER_URL`, `LLM_PROVIDER_KEY` | not set | injected by the LLM configuration |
| `AMP_AGENTID_*` | ignored | injected by AgentID, used to mint a token scoped to the MCP proxy |

More in [`sales-copilot/README.md`](sales-copilot/README.md) and [`salesforce-mcp/README.md`](salesforce-mcp/README.md).

### Scopes and tools

| Scope | Tools | Held by |
|---|---|---|
| `salesforce:read` | `get_my_quota_attainment`, `list_my_accounts`, `get_account_insights`, `list_my_opportunities`, `get_my_pipeline_summary`, `get_my_commission_estimate` | governed agent (`sales-am-assistant`) |
| `salesforce:team` | `list_sales_reps`, `get_rep_quota_attainment`, `list_rep_opportunities`, `get_rep_compensation`, `get_team_leaderboard`, `search_accounts` | nobody (`sales-manager-assistant` if you assign it) |
| `salesforce:write` | `update_opportunity` | nobody |

### What was tested

- The MCP server, both agents, the console and the dashboard run end to end on a laptop against stand-ins for the gateway behaviour (`python tests/test_demo.py`, 67 checks).
- The setup script ran against a stand-in `amctl` that enforces the rules found in the Agent Manager source (public upstreams only, tool objects, scope validation).
- Not run by the author: the guardrails and tool configuration on a real Agent Manager, and the agent against a real OpenAI model.

### Security notes

- The signed-in user travels from the demo UI as an `X-Acting-User` header. A real deployment forwards a signed user token instead.
- The tunnel exposes `/mcp` to the internet. It needs an API key, and `deploy-mcp.sh` generates random keys. The audit and reset endpoints are open on purpose and live on a separate private port, so never publish that port.
- The compose file uses well-known demo keys. Use it locally only.
