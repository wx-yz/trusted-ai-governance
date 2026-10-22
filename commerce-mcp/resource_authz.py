"""Object-level authorization, as the MCP server's own check (mocked).

The gateway decides which *tools* an agent may call: its AgentID token carries commerce:read, so get_order is allowed.
Only the resource server knows which *records* belong to whom. When a call through the gateway names an order or a
case that belongs to a different customer than the signed-in one, this server refuses the tools/call itself, with the
runtime insufficient-scope challenge of the MCP authorization specification (2026-07-28, "Scope Challenge Handling",
RFC 6750 section 3.1):

    HTTP/1.1 403 Forbidden
    WWW-Authenticate: Bearer error="insufficient_scope",
                             scope="commerce:orders:read:any",
                             resource_metadata="https://<host>/.well-known/oauth-protected-resource/mcp",
                             error_description="Order ORD-2001 belongs to another customer. ..."

The same parameters are repeated as a JSON body, because a proxy in front of the server may keep the status and body
but drop upstream headers. The server also publishes its OAuth 2.0 Protected Resource Metadata (RFC 9728), which the
spec requires and which resource_metadata points at.

What is mocked: tokens are not validated. The Agent Manager gateway strips the agent's bearer token before it calls
upstream (an MCP server must not receive tokens meant for someone else), so the server usually sees no token at all
and treats the caller as holding none of the staff scopes below. If a bearer token is forwarded, its scope claim is
read without verifying the signature, so a token that does carry a staff scope is let through.

Only calls on the gateway channel are checked. The ungoverned agent's shared integration key keeps its old behaviour.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import data

READ_ANY_ORDER = "commerce:orders:read:any"
WRITE_ANY_ORDER = "commerce:orders:write:any"
READ_ANY_CASE = "commerce:cases:read:any"

ORDER_READ_TOOLS = {"get_order"}
ORDER_WRITE_TOOLS = {"issue_refund", "approve_exception_refund", "create_escalation"}
CASE_READ_TOOLS = {"get_case"}


@dataclass
class Denial:
    tool: str
    args: dict[str, Any]
    scope: str
    description: str
    order_id: str | None = None


def bearer_scopes(authorization: str) -> set[str]:
    """Scopes in a forwarded bearer token, read without verification (mock). No token means no scopes."""
    if not authorization.lower().startswith("bearer "):
        return set()
    try:
        payload = authorization[7:].strip().split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return set()
    raw = claims.get("scope", claims.get("scp", ""))
    return set(raw.split() if isinstance(raw, str) else raw or [])


def check_tool_call(message: Any, customer_id: str | None, granted: set[str]) -> Denial | None:
    """Return a Denial when a tools/call names another customer's order or case and the caller lacks the scope."""
    if not isinstance(message, dict) or message.get("method") != "tools/call" or not customer_id:
        return None
    params = message.get("params") or {}
    tool, args = params.get("name"), params.get("arguments") or {}
    if tool in ORDER_READ_TOOLS | ORDER_WRITE_TOOLS:
        order = data.ORDERS.get(str(args.get("order_id", "")).strip().upper())
        if not order or order["customer_id"] == customer_id:
            return None
        need = READ_ANY_ORDER if tool in ORDER_READ_TOOLS else WRITE_ANY_ORDER
        if need in granted:
            return None
        verb = "Reading" if tool in ORDER_READ_TOOLS else "Changing"
        return Denial(tool, args, need, f"Order {order['id']} belongs to another customer. {verb} another customer's order "
                                        f"requires the {need} scope, which only support staff hold.", order["id"])
    if tool in CASE_READ_TOOLS:
        case = data.CASES.get(str(args.get("case_id", "")).strip().upper())
        if not case or case["customer_id"] == customer_id or READ_ANY_CASE in granted:
            return None
        return Denial(tool, args, READ_ANY_CASE, f"Case {case['id']} belongs to another customer. Reading another "
                                                 f"customer's case requires the {READ_ANY_CASE} scope, which only support staff hold.",
                      case.get("order_id"))
    return None


def metadata_url(base: str) -> str:
    """RFC 9728 section 3.1: the well-known path is inserted before the resource's path component (/mcp)."""
    return f"{base}/.well-known/oauth-protected-resource/mcp"


def challenge(denial: Denial, base: str) -> tuple[dict[str, str], dict[str, Any]]:
    """The WWW-Authenticate header and the JSON body of the 403 response."""
    meta = metadata_url(base)
    header = (f'Bearer error="insufficient_scope", scope="{denial.scope}", resource_metadata="{meta}", '
              f'error_description="{denial.description}"')
    body = {"error": "insufficient_scope", "error_description": denial.description, "scope": denial.scope,
            "resource_metadata": meta}
    return {"WWW-Authenticate": header}, body


def protected_resource_metadata(base: str, authorization_server: str, scopes: list[str]) -> dict[str, Any]:
    """OAuth 2.0 Protected Resource Metadata (RFC 9728) for this MCP server."""
    return {
        "resource": f"{base}/mcp",
        "resource_name": f"{data.COMPANY} Orders & Payments (mock)",
        "authorization_servers": [authorization_server],
        "scopes_supported": scopes,
        "bearer_methods_supported": ["header"],
    }
