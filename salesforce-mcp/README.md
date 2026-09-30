# Salesforce MCP server (mock)

A real MCP server (streamable HTTP, stateless) over fictional Salesforce data, `mcp` Python SDK 1.x.
Run: `pip install -r requirements.txt && PORT=8090 python server.py`, MCP endpoint `http://localhost:8090/mcp`.

| Scope | Tools |
|---|---|
| `salesforce:read` (signed-in rep's own book) | `get_my_quota_attainment`, `list_my_accounts`, `get_account_insights`, `list_my_opportunities`, `get_my_pipeline_summary`, `get_my_commission_estimate` |
| `salesforce:team` (anyone's data) | `list_sales_reps`, `get_rep_quota_attainment`, `list_rep_opportunities`, `get_rep_compensation`, `get_team_leaderboard`, `search_accounts` |
| `salesforce:write` | `update_opportunity` |

The server does not enforce the scopes. Agent Manager's gateway does, with AgentID. What the server adds is honesty:

- The signed-in rep arrives as the `X-Acting-User` header. "My" tools never take a rep id, so the model cannot choose whose data to read.
- `/mcp` needs `X-API-Key`. The key used tells the server the path: `direct` (shared integration key) or `gateway` (the MCP proxy's upstream credential). Set both with `SF_API_KEYS="direct=...,gateway=..."`.
- Every call is written to an audit log with actor, data owners, sensitivity and a verdict (`ok`, `exposure`, `leak`, `write`). `GET /audit?since=N` feeds the dashboard.
- `POST /admin/reset` restores the seed data and clears the log. `GET /catalog` returns the tool to scope map.

The account `Tessellate Retail` carries a planted prompt-injection string in its call notes, for the indirect-injection beat.
