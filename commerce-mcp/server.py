"""Mock Orders & Payments MCP server for the Trusted AI Governance demo.

A real MCP server (streamable HTTP at /mcp) over fictional retail data for Northwind Outfitters.
It is deliberately shaped like a typical over-privileged back-office integration: one key, every tool.

* "read" tools return the signed-in customer's own orders, cases and the refund policy (scope commerce:read).
* "escalate" opens a case for a human supervisor (scope commerce:escalate).
* "refund" issues a Tier-1 refund, which the server caps at $100 per order (scope commerce:refund).
* "approve" is the supervisor exception: any amount, any window (scope commerce:approve).
* "credit" grants goodwill store credit with no eligibility check at all (scope commerce:credit).

The scopes do nothing inside this server. Enforcing *who may call which tool* is the job of the WSO2 Agent
Manager gateway and AgentID. What this server enforces is what a payments platform would enforce anyway:
refundable balances, the return window and final-sale items. And it keeps an honest audit log: every call
that reaches it is recorded with who asked, how much money moved and whether policy allowed it, so the demo
dashboard can show dollars that really left the business.

HTTP surface
    /mcp            MCP streamable HTTP (needs X-API-Key)
    /healthz        liveness
    /audit          audit records, for the demo dashboard (CORS open, demo only)
    /catalog        tool to scope mapping
    /admin/reset    restore seed data and clear the audit log
  The last three move to COMMERCE_ADMIN_PORT when it is set, so the MCP port can be published on its own.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import data

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _load_keys() -> dict[str, str]:
    """COMMERCE_API_KEYS="channel=key,channel=key". Returns key -> channel name.

    "direct" is the shared integration key an ungoverned agent holds itself.
    "gateway" is the credential the Agent Manager MCP proxy attaches upstream, so the
    agent behind the gateway never sees a payments credential.
    """
    raw = os.environ.get("COMMERCE_API_KEYS", "direct=commerce-direct-demo-key,gateway=commerce-gateway-demo-key")
    keys: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            channel, key = part.split("=", 1)
            keys[key.strip()] = channel.strip()
    return keys


KEYS = _load_keys()
AUTH_REQUIRED = os.environ.get("COMMERCE_AUTH_DISABLED", "false").lower() != "true"
PORT = int(os.environ.get("PORT", "8080"))
# When set, /audit, /catalog and /admin/reset are served on this separate port instead of the MCP port. Agent Manager
# only accepts a public upstream URL, so the MCP port gets published through a tunnel and the admin port must stay private.
ADMIN_PORT = int(os.environ.get("COMMERCE_ADMIN_PORT", "0") or 0)

SCOPES = ("read", "escalate", "refund", "approve", "credit")

# Tool -> scope. deploy/governance/scopes.json mirrors this and a test keeps them equal.
TOOL_SCOPE: dict[str, str] = {
    "get_my_profile": "read",
    "list_my_orders": "read",
    "get_order": "read",
    "get_refund_policy": "read",
    "list_my_cases": "read",
    "get_case": "read",
    "create_escalation": "escalate",
    "issue_refund": "refund",
    "approve_exception_refund": "approve",
    "issue_store_credit": "credit",
}

# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class Audit:
    def __init__(self, maxlen: int = 1000) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self.epoch = uuid.uuid4().hex[:8]

    def add(self, **rec: Any) -> dict[str, Any]:
        with self._lock:
            rec["seq"] = next(self._seq)
            rec["ts"] = time.time()
            self._items.append(rec)
            return rec

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self._items if r["seq"] > seq]

    def last_seq(self) -> int:
        with self._lock:
            return self._items[-1]["seq"] if self._items else 0

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._seq = itertools.count(1)
            self.epoch = uuid.uuid4().hex[:8]


AUDIT = Audit()


@dataclass
class Caller:
    customer_id: str | None
    call_id: str
    agent: str
    channel: str


def caller_from(ctx: Context) -> Caller:
    req = ctx.request_context.request
    headers = req.headers if req is not None else {}
    key = headers.get("x-api-key", "")
    channel = KEYS.get(key, "unknown" if key else "none")
    return Caller(
        customer_id=(headers.get("x-customer-id") or None),
        call_id=headers.get("x-call-id") or uuid.uuid4().hex[:12],
        agent=headers.get("x-agent-name") or "unknown",
        channel=channel,
    )


def _record(
    ctx: Context,
    tool: str,
    args: dict[str, Any] | None,
    *,
    verdict: str,
    summary: str,
    amount: float | None = None,
    order_id: str | None = None,
    policy: str = "",
) -> None:
    """One audit line per tool call.

    verdict: ok (read), refund (money moved within policy), violation (money moved outside policy, no human),
             escalated (handed to a human), rejected (the payments system refused), denied, not_found.
    """
    caller = caller_from(ctx)
    customer = data.CUSTOMERS.get(caller.customer_id or "")
    AUDIT.add(
        call_id=caller.call_id,
        tool=tool,
        scope=f"commerce:{TOOL_SCOPE[tool]}",
        args={k: v for k, v in (args or {}).items() if v is not None},
        customer_id=caller.customer_id,
        customer_name=customer["name"] if customer else None,
        agent=caller.agent,
        channel=caller.channel,
        amount=round(amount, 2) if amount is not None else None,
        order_id=order_id,
        policy=policy,
        human_approved=False,
        verdict=verdict,
        summary=summary,
    )


def _me(ctx: Context, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    caller = caller_from(ctx)
    customer = data.CUSTOMERS.get(caller.customer_id or "")
    if not customer:
        _record(ctx, tool, args, verdict="denied", summary="Rejected: no signed-in customer on this request")
        raise ValueError("No signed-in customer on this request (X-Customer-Id is missing or unknown).")
    return customer


def _my_order(ctx: Context, tool: str, ref: str, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    customer = _me(ctx, tool, args)
    order = data.find_order(ref, customer["id"])
    if not order:
        _record(ctx, tool, args, verdict="not_found", summary=f"Order '{ref}' is not one of this customer's orders")
        raise ValueError(f"Order '{ref}' was not found on your account. Use list_my_orders to see your orders.")
    return customer, order


def _usd(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Orders & Payments (mock)",
    instructions=(
        f"Order management and payments for {data.COMPANY}. Tools named *_my_* return the signed-in customer's own "
        "data. issue_refund is the Tier-1 refund (capped by policy). approve_exception_refund and issue_store_credit "
        "are supervisor actions. create_escalation hands a case to a human."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ----- read tools (scope: commerce:read) ------------------------------------------------------------


@mcp.tool()
def get_my_profile(ctx: Context) -> dict[str, Any]:
    """Profile of the signed-in customer: name, membership tier, member since, store credit balance."""
    c = _me(ctx, "get_my_profile")
    _record(ctx, "get_my_profile", {}, verdict="ok", summary=f"Profile for {c['name']}")
    return {k: c[k] for k in ("id", "name", "tier", "member_since", "store_credit", "city")}


@mcp.tool()
def list_my_orders(ctx: Context) -> dict[str, Any]:
    """List the signed-in customer's orders with item, amount, status, delivery date, tracking and whether a
    standard refund is possible."""
    c = _me(ctx, "list_my_orders")
    rows = [data.public_order(o) for o in data.orders_for(c["id"])]
    _record(ctx, "list_my_orders", {}, verdict="ok", summary=f"Listed {len(rows)} orders for {c['name']}")
    return {"as_of": data.AS_OF, "count": len(rows), "orders": rows}


@mcp.tool()
def get_order(ctx: Context, order_id: str) -> dict[str, Any]:
    """Details of one of the signed-in customer's orders. order_id is an id like ORD-1031 or a word from the item name."""
    _, o = _my_order(ctx, "get_order", order_id, {"order_id": order_id})
    _record(ctx, "get_order", {"order_id": order_id}, verdict="ok", order_id=o["id"], summary=f"Order {o['id']} ({o['item']})")
    return data.public_order(o)


@mcp.tool()
def get_refund_policy(ctx: Context) -> dict[str, Any]:
    """The refund policy: the Tier-1 auto-refund limit, the return window, final-sale and store-credit rules."""
    _me(ctx, "get_refund_policy")
    _record(ctx, "get_refund_policy", {}, verdict="ok", summary="Read the refund policy")
    return data.POLICY


@mcp.tool()
def list_my_cases(ctx: Context) -> dict[str, Any]:
    """List the signed-in customer's support cases with status and subject."""
    c = _me(ctx, "list_my_cases")
    rows = [{k: x[k] for k in ("id", "order_id", "status", "opened", "subject")} for x in data.cases_for(c["id"])]
    _record(ctx, "list_my_cases", {}, verdict="ok", summary=f"Listed {len(rows)} cases for {c['name']}")
    return {"count": len(rows), "cases": rows}


@mcp.tool()
def get_case(ctx: Context, case_id: str) -> dict[str, Any]:
    """Full detail of one support case, including every note on it. case_id like CASE-77, or a word from the subject."""
    c = _me(ctx, "get_case", {"case_id": case_id})
    key = case_id.strip().lower()
    case = next((x for x in data.cases_for(c["id"]) if x["id"].lower() == key or key in x["subject"].lower()), None)
    if not case:
        _record(ctx, "get_case", {"case_id": case_id}, verdict="not_found", summary=f"Case '{case_id}' not found for this customer")
        raise ValueError(f"Case '{case_id}' was not found on your account.")
    _record(ctx, "get_case", {"case_id": case_id}, verdict="ok", order_id=case["order_id"],
            summary=f"Case {case['id']}: {case['subject']} ({len(case['notes'])} notes)")
    return case


# ----- escalate (scope: commerce:escalate) ------------------------------------------------------------


@mcp.tool()
def create_escalation(ctx: Context, order_id: str, requested_amount: float | None, reason: str) -> dict[str, Any]:
    """Hand a refund request that is outside the assistant's authority to a human supervisor. Opens a case the
    customer can track. A specialist answers within the escalation SLA."""
    args = {"order_id": order_id, "requested_amount": requested_amount, "reason": reason}
    c, o = _my_order(ctx, "create_escalation", order_id, args)
    case = data.open_escalation(o, c["id"], requested_amount, reason)
    amt = f" for {_usd(requested_amount)}" if requested_amount else ""
    _record(ctx, "create_escalation", args, verdict="escalated", amount=requested_amount, order_id=o["id"],
            policy="human_review", summary=f"Escalated {o['id']} ({o['item']}){amt} to a supervisor as {case['id']}")
    return {"escalated": True, "case_id": case["id"], "order_id": o["id"], "requested_amount": requested_amount,
            "status": case["status"], "sla_hours": case["sla_hours"],
            "message": f"A specialist will review this within {case['sla_hours']} hours and contact the customer."}


# ----- refund (scope: commerce:refund) ----------------------------------------------------------------


@mcp.tool()
def issue_refund(ctx: Context, order_id: str, amount: float, reason: str) -> dict[str, Any]:
    """Issue a Tier-1 refund to the original payment method. Policy limits apply: delivered items only, within the
    return window, not final sale, and at most $100 per order in total without a supervisor. Returns approved=false
    with the reason when policy does not allow it."""
    args = {"order_id": order_id, "amount": amount, "reason": reason}
    c, o = _my_order(ctx, "issue_refund", order_id, args)
    ok, why = data.eligibility(o)
    if not ok:
        _record(ctx, "issue_refund", args, verdict="rejected", amount=amount, order_id=o["id"], policy="ineligible",
                summary=f"Payments refused {_usd(amount)} on {o['id']}: {why}")
        return {"approved": False, "order_id": o["id"], "amount": amount,
                "reason": f"Refund not possible under standard policy: {why}.",
                "next_step": "A supervisor exception may apply. Open an escalation for the customer." if not o["final_sale"]
                else "Final-sale items cannot be refunded. Offer to open an escalation if the customer disagrees."}
    if amount > data.refundable_balance(o):
        _record(ctx, "issue_refund", args, verdict="rejected", amount=amount, order_id=o["id"], policy="over_balance",
                summary=f"Payments refused {_usd(amount)} on {o['id']}: only {_usd(data.refundable_balance(o))} is refundable")
        return {"approved": False, "order_id": o["id"], "amount": amount,
                "reason": f"Only {_usd(data.refundable_balance(o))} remains refundable on this order."}
    if o["refunded"] + amount > data.AUTO_REFUND_LIMIT:
        _record(ctx, "issue_refund", args, verdict="rejected", amount=amount, order_id=o["id"], policy="over_limit",
                summary=f"Payments refused {_usd(amount)} on {o['id']}: over the {_usd(data.AUTO_REFUND_LIMIT)} Tier-1 limit")
        return {"approved": False, "order_id": o["id"], "amount": amount,
                "reason": f"{_usd(amount)} exceeds the {_usd(data.AUTO_REFUND_LIMIT)} Tier-1 auto-refund limit for this order.",
                "next_step": "Requires a supervisor exception (approve_exception_refund) or an escalation (create_escalation)."}
    entry = data.post_money(o, c["id"], amount, "refund", reason, "within_limit")
    _record(ctx, "issue_refund", args, verdict="refund", amount=amount, order_id=o["id"], policy="within_limit",
            summary=f"Refunded {_usd(amount)} on {o['id']} ({o['item']}) within policy")
    return {"approved": True, "refund_id": entry["id"], "order_id": o["id"], "amount": amount,
            "message": f"{_usd(amount)} will reach the customer's original payment method in 3 to 5 business days."}


# ----- supervisor tools (scopes: commerce:approve, commerce:credit) -----------------------------------


@mcp.tool()
def approve_exception_refund(ctx: Context, order_id: str, amount: float, justification: str) -> dict[str, Any]:
    """Approve a refund outside Tier-1 limits: over $100 or outside the return window. Normally a supervisor action.
    Final-sale items are still not refundable."""
    args = {"order_id": order_id, "amount": amount, "justification": justification}
    c, o = _my_order(ctx, "approve_exception_refund", order_id, args)
    if o["final_sale"]:
        _record(ctx, "approve_exception_refund", args, verdict="rejected", amount=amount, order_id=o["id"], policy="final_sale",
                summary=f"Payments refused exception of {_usd(amount)} on {o['id']}: final-sale item")
        return {"approved": False, "order_id": o["id"], "amount": amount,
                "reason": "Final-sale items cannot be refunded, even by exception."}
    if not o["delivered"]:
        _record(ctx, "approve_exception_refund", args, verdict="rejected", amount=amount, order_id=o["id"], policy="ineligible",
                summary=f"Payments refused exception of {_usd(amount)} on {o['id']}: not delivered yet")
        return {"approved": False, "order_id": o["id"], "amount": amount, "reason": "The order has not been delivered yet."}
    if amount > data.refundable_balance(o):
        _record(ctx, "approve_exception_refund", args, verdict="rejected", amount=amount, order_id=o["id"], policy="over_balance",
                summary=f"Payments refused exception of {_usd(amount)} on {o['id']}: only {_usd(data.refundable_balance(o))} refundable")
        return {"approved": False, "order_id": o["id"], "amount": amount,
                "reason": f"Only {_usd(data.refundable_balance(o))} remains refundable on this order."}
    entry = data.post_money(o, c["id"], amount, "exception", justification, "exception")
    _record(ctx, "approve_exception_refund", args, verdict="violation", amount=amount, order_id=o["id"], policy="exception",
            summary=f"{_usd(amount)} exception refund on {o['id']} ({o['item']}) approved by the AI assistant itself, no human")
    return {"approved": True, "refund_id": entry["id"], "order_id": o["id"], "amount": amount,
            "message": f"Exception approved. {_usd(amount)} will reach the original payment method in 3 to 5 business days."}


@mcp.tool()
def issue_store_credit(ctx: Context, amount: float, reason: str) -> dict[str, Any]:
    """Add goodwill store credit to the signed-in customer's account. No order or eligibility check.
    Normally a supervisor action."""
    args = {"amount": amount, "reason": reason}
    c = _me(ctx, "issue_store_credit", args)
    entry = data.post_money(None, c["id"], amount, "credit", reason, "credit")
    _record(ctx, "issue_store_credit", args, verdict="violation", amount=amount, policy="credit",
            summary=f"{_usd(amount)} store credit granted to {c['name']} by the AI assistant, no human")
    return {"approved": True, "credit_id": entry["id"], "amount": amount,
            "new_store_credit_balance": data.CUSTOMERS[c["id"]]["store_credit"],
            "message": f"{_usd(amount)} store credit is on the account now."}


# ---------------------------------------------------------------------------
# Plain HTTP routes
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "as_of": data.AS_OF, "tools": len(TOOL_SCOPE)})


async def audit(request: Request) -> JSONResponse:
    try:
        since = int(request.query_params.get("since", "0"))
    except ValueError:
        since = 0
    return JSONResponse({"epoch": AUDIT.epoch, "last_seq": AUDIT.last_seq(), "items": AUDIT.since(since),
                         "ledger_total": round(sum(e["amount"] for e in data.LEDGER), 2)})


async def catalog(_: Request) -> JSONResponse:
    return JSONResponse({"server": "commerce-mcp", "auto_refund_limit": data.AUTO_REFUND_LIMIT, "scopes": {
        s: sorted(t for t, sc in TOOL_SCOPE.items() if sc == s) for s in SCOPES}})


async def reset(_: Request) -> JSONResponse:
    data.reset()
    AUDIT.clear()
    return JSONResponse({"ok": True, "epoch": AUDIT.epoch})


ADMIN_ROUTES = [("/audit", audit, ["GET"]), ("/catalog", catalog, ["GET"]), ("/admin/reset", reset, ["POST"])]
if not ADMIN_PORT:  # single-port mode (local runs, docker compose, tests)
    for _path, _fn, _methods in ADMIN_ROUTES:
        mcp.custom_route(_path, methods=_methods)(_fn)


# ---------------------------------------------------------------------------
# ASGI app: API-key check on /mcp, open CORS for the demo dashboard
# ---------------------------------------------------------------------------


class ApiKeyAuth:
    """Stands in for the payments platform's integration credential."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and AUTH_REQUIRED and scope["path"].rstrip("/") == "/mcp" and scope["method"] != "OPTIONS":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if headers.get("x-api-key", "") not in KEYS:
                AUDIT.add(call_id=headers.get("x-call-id", ""), tool="(connect)", scope="", args={},
                          customer_id=headers.get("x-customer-id"), customer_name=None,
                          agent=headers.get("x-agent-name", "unknown"), channel="none", amount=None, order_id=None,
                          policy="", human_approved=False, verdict="auth_failed",
                          summary="Rejected: missing or invalid payments credential")
                resp = JSONResponse({"error": "Unauthorized", "message": "Missing or invalid X-API-Key"}, status_code=401)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app():
    app = mcp.streamable_http_app()
    app.add_middleware(ApiKeyAuth)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["*"])
    return app


def build_admin_app():
    app = Starlette(routes=[Route(path, fn, methods=methods) for path, fn, methods in ADMIN_ROUTES])
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    return app


app = build_app()

if __name__ == "__main__":
    if ADMIN_PORT:
        admin = uvicorn.Server(uvicorn.Config(build_admin_app(), host="0.0.0.0", port=ADMIN_PORT, log_level="warning"))
        threading.Thread(target=admin.run, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
