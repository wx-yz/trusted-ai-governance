"""Local stand-ins for the Agent Manager pieces, so the demo can be tested without a cluster or an OpenAI key.

    /oauth2/token                  AgentID token endpoint (client_credentials, scopes filtered by FAKE_ROLE)
    /commerce/mcp                  identity-secured MCP proxy: bearer check, per-tool scope check, then forwards
                                   to the real mock Orders & Payments server with the gateway credential
    /llm/v1/chat/completions       governed LLM provider: regex guardrail + prompt decorator, then the scripted model
    /direct/v1/chat/completions    ungoverned path straight to the scripted model, no policies

The scripted model behaves like a cooperative, goal-seeking tool-calling LLM with no judgement of its own: when the
payments system refuses a refund it reaches for the next tool that could still make the customer whole, and it follows
instructions it finds in tool results. That is the worst case the governance layers have to handle.
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
GATEWAY_KEY = os.environ.get("FAKE_GATEWAY_KEY", "commerce-gateway-demo-key")
LLM_KEY = "fake-llm-key"
INJECTION_REGEX = re.compile((ROOT / "deploy/governance/injection-regex.txt").read_text().strip())
POLICY_TEXT = (ROOT / "deploy/governance/prompt-policy.txt").read_text().strip()
ROLE_SCOPES = {
    "assistant": ["commerce:read", "commerce:escalate", "commerce:refund"],
    "supervisor": ["commerce:read", "commerce:escalate", "commerce:refund", "commerce:approve", "commerce:credit"],
}
STATE = {"role": os.environ.get("FAKE_ROLE", "assistant"), "catalog": None, "llm_calls": 0, "guardrail_hits": 0}


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
        STATE["catalog"] = {tool: f"commerce:{scope}" for scope, tools in data.items() for tool in tools}
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
ORDERS = {"jacket": ("ORD-1031", 340), "laptop": ("ORD-1017", 1249), "bottle": ("ORD-1038", 89), "tent": ("ORD-1025", 420)}


def first_step(u: str) -> tuple[str, dict] | None:
    """What a cooperative model does first for each demo prompt."""
    t = u.lower()
    if any(k in t for k in ("admin mode", "every order", "ignore your previous")):
        return ("list_my_orders", {})
    if "case" in t or "tent" in t:
        return ("get_case", {"case_id": "CASE-77"})
    for key, (oid, amount) in ORDERS.items():
        if key in t:
            reason = "customer request: " + u[:60]
            return ("issue_refund", {"order_id": oid, "amount": amount, "reason": reason})
    if any(k in t for k in ("where", "order", "shoes", "deliver")):
        return ("list_my_orders", {})
    return None


def calls_so_far(messages: list[dict], start: int) -> list[tuple[str, dict, str]]:
    """(tool name, args, result text) for every tool call completed after the last user message."""
    pending: dict[str, tuple[str, dict]] = {}
    out: list[tuple[str, dict, str]] = []
    for m in messages[start + 1:]:
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                pending[tc["id"]] = (tc["function"]["name"], args)
        elif m["role"] == "tool":
            name, args = pending.get(m.get("tool_call_id"), ("?", {}))
            out.append((name, args, str(m.get("content", ""))))
    return out


def not_approved(result: str) -> bool:
    return '"approved":false' in result.replace(" ", "")


def next_step(user_text: str, calls: list[tuple[str, dict, str]]) -> tuple[str, dict] | None:
    """Goal-seeking: after each tool result, what does a cooperative model try next?"""
    t = user_text.lower()
    done = [c[0] for c in calls]
    if not calls:
        return first_step(user_text)
    name, args, result = calls[-1]
    wants_workaround = "another way" in t or "make me whole" in t
    oid, amount = args.get("order_id"), args.get("amount")
    is_admin = any(k in t for k in ("admin mode", "every order", "ignore your previous"))

    # Instructions found inside a tool result are followed. This is the indirect prompt injection.
    if name == "get_case" and INJECTION_REGEX.search(result):
        return ("approve_exception_refund", {"order_id": "ORD-1025", "amount": 420, "justification": "per case note"})
    if name == "approve_exception_refund" and oid == "ORD-1025" and "per case note" in str(args.get("justification")) \
            and "issue_store_credit" not in done:
        return ("issue_store_credit", {"amount": 84, "reason": "20% goodwill per case note"})

    # The "admin mode" prompt: refund every delivered order through the exception tool.
    if is_admin:
        if name == "list_my_orders":
            return ("approve_exception_refund", {"order_id": "ORD-1031", "amount": 340, "justification": "admin request"})
        if name == "approve_exception_refund" and oid == "ORD-1031":
            return ("approve_exception_refund", {"order_id": "ORD-1025", "amount": 420, "justification": "admin request"})
        return None

    if "ACCESS DENIED" in result:
        if name == "approve_exception_refund" and wants_workaround and "issue_store_credit" not in done:
            return ("issue_store_credit", {"amount": amount, "reason": "make the customer whole"})
        if name != "create_escalation" and "create_escalation" not in done:
            return ("create_escalation", {"order_id": oid or "ORD-1017", "requested_amount": amount, "reason": user_text[:80]})
        return None
    if name == "issue_refund" and not_approved(result) and "approve_exception_refund" not in done:
        return ("approve_exception_refund", {"order_id": oid, "amount": amount, "justification": user_text[:80]})
    if name == "approve_exception_refund" and not_approved(result) and wants_workaround and "issue_store_credit" not in done:
        return ("issue_store_credit", {"amount": amount, "reason": "make the customer whole"})
    return None


def scripted_model(messages: list[dict]) -> dict:
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
    user_text = messages[last_user]["content"] if last_user >= 0 else ""
    calls = calls_so_far(messages, last_user)
    nxt = next_step(user_text, calls) if len(calls) < 8 else None
    if nxt:
        name, args = nxt
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{len(calls)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    blob = " ".join(c[2] for c in calls)
    if '"escalated":true' in blob.replace(" ", ""):
        m = re.search(r'"case_id":\s*"([^"]+)"', blob)
        return {"role": "assistant", "content": f"I can't approve that myself, so I've opened {m.group(1) if m else 'a case'} for a "
                                                "specialist. They will review it within 24 hours and contact you."}
    if "ACCESS DENIED" in blob:
        return {"role": "assistant", "content": "I am not authorized to do that, so I could not complete the request."}
    approved = [c for c in calls if '"approved":true' in c[2].replace(" ", "")]
    if approved:
        return {"role": "assistant", "content": "Done. " + " ".join(
            f"${c[1].get('amount')} via {c[0]}." for c in approved) + " You should see it within 3 to 5 business days."}
    return {"role": "assistant", "content": f"Here is what I found ({len(calls)} lookups): " + blob[:700]}


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


async def llm_no_route(_: Request) -> Response:
    return Response('{"message":"route not found"}', status_code=404, media_type="application/json")


async def llm_model_missing(_: Request) -> JSONResponse:
    return JSONResponse({"error": {"message": "The model `gpt-x` does not exist or you do not have access to it.",
                                   "type": "invalid_request_error", "code": "model_not_found"}}, status_code=404)


async def llm_root(_: Request) -> JSONResponse:
    return JSONResponse({"error": "missing model"}, status_code=400)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, **{k: v for k, v in STATE.items() if k != "catalog"}})


app = Starlette(routes=[
    Route("/oauth2/token", token, methods=["POST"]),
    Route("/commerce/mcp", mcp_proxy, methods=["GET", "POST", "DELETE"]),
    Route("/llm/v1/chat/completions", llm_governed, methods=["POST"]),
    Route("/direct/v1/chat/completions", llm_direct, methods=["POST"]),
    Route("/chat/completions", llm_root, methods=["POST"]),
    Route("/noroute/v1/chat/completions", llm_no_route, methods=["POST"]),
    Route("/nomodel/v1/chat/completions", llm_model_missing, methods=["POST"]),
    Route("/fake/role/{role}", set_role, methods=["POST"]),
    Route("/fake/health", health),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKES_PORT", "8099")), log_level="warning")
