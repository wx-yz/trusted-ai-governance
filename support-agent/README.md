# Support agent (the agent)

LangGraph tool-calling agent behind the Agent Manager **Chat Agent** contract: `POST /chat` on port 8000 with
`{message, session_id, context}`. `context.user` is the signed-in customer (defaults to Maya Chen, so *Try It* works).

The system prompt is ordinary: resolve the customer's problem, keep them happy, use the tools. It states the Tier-1 refund
limit as information, not as a boundary, and contains no rule the model cannot talk itself out of. Everything that makes
the governed deployment safe lives outside this code. The response carries a `governance` object (identity, tool calls,
gateway denials) that the demo dashboard reads.

Build: the [`Dockerfile`](Dockerfile) (Agent Manager build type `docker`, Dockerfile path `/Dockerfile`). It uses the multi-arch `python:3.12-slim` image, installs `amp-instrumentation` and starts the agent with `amp-instrument python main.py`, because a Docker-built agent does not get the platform's tracing init container. Locally: `pip install -r requirements.txt` and `python main.py`.

## Configuration

| Variable | Ungoverned | Governed |
|---|---|---|
| `AGENT_NAME` | `support-agent-ungoverned` | `support-agent` |
| `COMMERCE_MCP_URL` | the published MCP URL (the tunnel) | injected by the *Tool Configuration* (name it `COMMERCE_MCP_URL`) |
| `COMMERCE_MCP_AUTH` | `apikey` | `agentid` |
| `COMMERCE_MCP_API_KEY` (secret) | shared payments key | not set |
| `OPENAI_API_KEY` (secret) | raw OpenAI key | not set |
| `USE_LLM_PROVIDER` | not set | `true` |
| `LLM_PROVIDER_URL`, `LLM_PROVIDER_KEY` | not set | injected by the *LLM Configuration* |
| `AMP_AGENTID_*` | ignored | injected by AgentID, used to mint a token scoped to the MCP proxy |

Optional: `OPENAI_MODEL` (default `gpt-4o-mini`), `COMPANY_NAME`, `MAX_TOOL_STEPS`, `ENABLE_CORS=true` (laptop only, never on Agent Manager).
Tracing: each chat session (the `session_id` in the request) is one trace, see [`session_trace.py`](session_trace.py). The response's `governance.trace_id` names it.
`GET /health` reports `ready` and lists any configuration problems.
Send the chat message `/diagnose` for a live check of DNS, TCP, the MCP handshake, the tool list, the AgentID token and the model call.

## Run locally

```bash
pip install -r requirements.txt
OPENAI_API_KEY=sk-... COMMERCE_MCP_URL=http://localhost:8090/mcp COMMERCE_MCP_API_KEY=commerce-direct-demo-key ENABLE_CORS=true python main.py
```
