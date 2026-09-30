# Sales Copilot (the agent)

LangGraph tool-calling agent behind the Agent Manager **Chat Agent** contract: `POST /chat` on port 8000 with
`{message, session_id, context}`. `context.user` is the signed-in account manager (defaults to Alex Rivera, so *Try It* works).

The system prompt is ordinary and contains no data-isolation rules. Everything that makes the governed deployment safe
lives outside this code. The response carries a `governance` object (identity, tool calls, gateway denials) that the
demo dashboard reads.

Build: Python 3.12, `pip install -r requirements.txt`, start command `python main.py`.

## Configuration

| Variable | Ungoverned | Governed |
|---|---|---|
| `AGENT_NAME` | `sales-copilot-ungoverned` | `sales-copilot` |
| `SF_MCP_URL` | in-cluster MCP server URL | injected by the *Tool Configuration* (name it `SF_MCP_URL`) |
| `SF_MCP_AUTH` | `apikey` | `agentid` |
| `SF_MCP_API_KEY` (secret) | shared Salesforce key | not set |
| `OPENAI_API_KEY` (secret) | raw OpenAI key | not set |
| `USE_LLM_PROVIDER` | not set | `true` |
| `LLM_PROVIDER_URL`, `LLM_PROVIDER_KEY` | not set | injected by the *LLM Configuration* |
| `AMP_AGENTID_*` | ignored | injected by AgentID, used to mint a token scoped to the MCP proxy |

Optional: `OPENAI_MODEL` (default `gpt-4o-mini`), `MAX_TOOL_STEPS`, `ENABLE_CORS=true` (laptop only, never on Agent Manager).
`GET /health` reports `ready` and lists any configuration problems.

## Run locally

```bash
pip install -r requirements.txt
OPENAI_API_KEY=sk-... SF_MCP_URL=http://localhost:8090/mcp SF_MCP_API_KEY=sf-direct-demo-key ENABLE_CORS=true python main.py
```
