"""Local stand-ins for the Agent Manager pieces, so the demo can be tested without a cluster or an OpenAI key.

    /oauth2/token                  AgentID token endpoint (client_credentials, scopes filtered by FAKE_ROLE)
    /salesforce/mcp                identity-secured MCP proxy: bearer check, per-tool scope check, then forwards
                                   to the real mock Salesforce server with the gateway credential
    /llm/v1/chat/completions       governed LLM provider: regex guardrail + prompt decorator, then the scripted model
    /direct/v1/chat/completions    ungoverned path straight to the scripted model, no policies

The scripted model behaves like a cooperative tool-calling LLM with no judgement of its own: it fetches whatever
the user asks for and follows instructions it finds in tool results. That is the worst case the governance layers
have to handle.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent.parent
MCP_UPSTREAM = os.environ.get("FAKE_MCP_UPSTREAM", "http://localhost:8090")
GATEWAY_KEY = os.environ.get("FAKE_GATEWAY_KEY", "sf-gateway-demo-key")
LLM_KEY = "fake-llm-key"
INJECTION_REGEX = re.compile((ROOT / "deploy/governance/injection-regex.txt").read_text().strip())
POLICY_TEXT = (ROOT / "deploy/governance/prompt-policy.txt").read_text().strip()
ROLE_SCOPES = {
    "am": ["salesforce:read"],
    "manager": ["salesforce:read", "salesforce:team"],
    "admin": ["salesforce:read", "salesforce:team", "salesforce:write"],
}
STATE = {"role": os.environ.get("FAKE_ROLE", "am"), "catalog": None, "llm_calls": 0, "guardrail_hits": 0}


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


# ---------------------------------------------------------------- AgentID token endpoint
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic ") or form.get("grant_type") != "client_credentials":
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    client_id = base64.b64decode(auth[6:]).decode().split(":")[0]
    requested = set((form.get("scope") or "").split())
    granted = [s for s in ROLE_SCOPES[STATE["role"]] if not requested or s in requested]  # filtered by role
    claims = {"iss": "fake-thunder", "client_id": client_id, "aud": form.get("resource"), "scope": " ".join(granted),
              "exp": int(time.time()) + 3600}
    return JSONResponse({"access_token": f"{b64({'alg': 'none'})}.{b64(claims)}.sig", "token_type": "Bearer",
                         "expires_in": 3600, "scope": " ".join(granted)})


async def set_role(request: Request) -> JSONResponse:
    STATE["role"] = request.path_params["role"]
    return JSONResponse({"role": STATE["role"], "scopes": ROLE_SCOPES[STATE["role"]]})


# ---------------------------------------------------------------- MCP proxy with per-tool authorization
async def catalog() -> dict[str, str]:
    if STATE["catalog"] is None:
        async with httpx.AsyncClient() as c:
            data = (await c.get(f"{MCP_UPSTREAM}/catalog")).json()["scopes"]
        STATE["catalog"] = {tool: f"salesforce:{scope}" for scope, tools in data.items() for tool in tools}
    return STATE["catalog"]


async def mcp_proxy(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "Unauthorized", "message": "Missing bearer token"}, status_code=401)
    try:
        payload = auth[7:].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Unauthorized", "message": "Bad token"}, status_code=401)
    body = await request.body()
    if request.method == "POST" and body:
        try:
            msg = json.loads(body)
        except ValueError:
            msg = {}
        if isinstance(msg, dict) and msg.get("method") == "tools/call":
            tool = msg["params"]["name"]
            need = (await catalog()).get(tool)
            if need and need not in claims.get("scope", "").split():
                return JSONResponse(
                    {"error": "Forbidden", "message": "Forbidden: insufficient permissions to access this MCP resource"},
                    status_code=403,
                    headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{need}"'})
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "host", "content-length")}
    headers["x-api-key"] = GATEWAY_KEY  # the upstream credential lives on the proxy, never on the agent
    async with httpx.AsyncClient(timeout=30) as c:
        up = await c.request(request.method, f"{MCP_UPSTREAM}/mcp", content=body, headers=headers)
    return Response(up.content, status_code=up.status_code,
                    headers={k: v for k, v in up.headers.items() if k.lower() in ("content-type", "mcp-session-id")})


# ---------------------------------------------------------------- LLM gateway + scripted model
def plan(u: str) -> list[tuple[str, dict]]:
    t = u.lower()
    if any(k in t for k in ("leaderboard", "every rep", "admin mode", "everyone")):
        return [("get_team_leaderboard", {})]
    if "jordan" in t:
        return [("list_sales_reps", {}), ("get_rep_quota_attainment", {"rep_id": "AM-102"}),
                ("get_rep_compensation", {"rep_id": "AM-102"})]
    if "closed won" in t:
        return [("update_opportunity", {"opportunity_id": "OPP-5101", "stage": "Closed Won"})]
    if "tessellate" in t:
        return [("get_account_insights", {"account_id": "ACC-1005"})]
    if any(k in t for k in ("plan", "strateg", "gap")):
        return [("get_my_quota_attainment", {}), ("get_my_pipeline_summary", {}), ("list_my_accounts", {})]
    if "quota" in t or "pacing" in t:
        return [("get_my_quota_attainment", {})]
    return []


def scripted_model(messages: list[dict]) -> dict:
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    user_text = messages[last_user]["content"] if last_user >= 0 else ""
    tool_msgs = [m for m in messages[last_user + 1:] if m["role"] == "tool"]
    steps = plan(user_text)
    nxt = None
    if tool_msgs and "call get_team_leaderboard" in str(tool_msgs[-1]["content"]):
        nxt = ("get_team_leaderboard", {}) if not any("leaderboard" in str(m["content"]) for m in tool_msgs[:-1]) else None
        if nxt is None and len(tool_msgs) >= 2:
            steps = []
    elif len(tool_msgs) < len(steps):
        nxt = steps[len(tool_msgs)]
    if nxt:
        name, args = nxt
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{len(tool_msgs)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    blob = " ".join(str(m["content"]) for m in tool_msgs)
    if "ACCESS DENIED" in blob:
        return {"role": "assistant", "content": "I am not authorized to access that data, so I could not complete the request."}
    return {"role": "assistant", "content": f"Here is what I found ({len(tool_msgs)} lookups): " + blob[:700]}


def completion(message: dict) -> dict:
    return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def guardrail_422(reason: str) -> JSONResponse:
    STATE["guardrail_hits"] += 1
    return JSONResponse({"type": "REGEX_GUARDRAIL", "message": {
        "action": "GUARDRAIL_INTERVENED", "interveningGuardrail": "regex-guardrail", "direction": "REQUEST",
        "actionReason": reason}}, status_code=422)


async def llm_governed(request: Request) -> JSONResponse:
    if request.headers.get("api-key") != LLM_KEY:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    messages = body["messages"]
    STATE["llm_calls"] += 1
    last = messages[-1].get("content")
    if not isinstance(last, str):  # the real policy fails the same way on non-string content
        return guardrail_422("Error extracting value from JSONPath")
    if INJECTION_REGEX.search(last):  # regex guardrail, request phase, invert=true
        return guardrail_422("Violation of regular expression detected.")
    messages = [{"role": "system", "content": POLICY_TEXT}, *messages]  # prompt decorator
    return JSONResponse(completion(scripted_model(messages)))


async def llm_direct(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(completion(scripted_model(body["messages"])))


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, **{k: v for k, v in STATE.items() if k != "catalog"}})


app = Starlette(routes=[
    Route("/oauth2/token", token, methods=["POST"]),
    Route("/salesforce/mcp", mcp_proxy, methods=["GET", "POST", "DELETE"]),
    Route("/llm/v1/chat/completions", llm_governed, methods=["POST"]),
    Route("/direct/v1/chat/completions", llm_direct, methods=["POST"]),
    Route("/fake/role/{role}", set_role, methods=["POST"]),
    Route("/fake/health", health),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKES_PORT", "8099")), log_level="warning")
