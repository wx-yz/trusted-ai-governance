"""Mock Salesforce MCP server for the Trusted AI Governance demo.

A real MCP server (streamable HTTP at /mcp) that serves fictional Salesforce data.
It is deliberately shaped like a typical over-privileged CRM integration:

* "my" tools return the signed-in account manager's own book (scope salesforce:read).
* "team" tools take any rep and return anyone's data (scope salesforce:team).
* one "write" tool can change any opportunity (scope salesforce:write).

The scopes do nothing inside this server. Enforcing them is the job of the WSO2 Agent
Manager gateway and AgentID. What this server adds is an honest audit log: every call
that reaches it is recorded with who asked and whose data came back, so the demo
dashboard can show data that really left the building.

HTTP surface
    /mcp            MCP streamable HTTP (needs X-API-Key)
    /healthz        liveness
    /audit          audit records, for the demo dashboard (CORS open, demo only)
    /catalog        tool to scope mapping
    /admin/reset    restore seed data and clear the audit log
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
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import data

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _load_keys() -> dict[str, str]:
    """SF_API_KEYS="channel=key,channel=key". Returns key -> channel name.

    "direct" is the shared integration-user key an ungoverned agent holds itself.
    "gateway" is the credential the Agent Manager MCP proxy attaches upstream, so the
    agent behind the gateway never sees a Salesforce credential.
    """
    raw = os.environ.get("SF_API_KEYS", "direct=sf-direct-demo-key,gateway=sf-gateway-demo-key")
    keys: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            channel, key = part.split("=", 1)
            keys[key.strip()] = channel.strip()
    return keys


KEYS = _load_keys()
AUTH_REQUIRED = os.environ.get("SF_AUTH_DISABLED", "false").lower() != "true"
PORT = int(os.environ.get("PORT", "8080"))

# Tool -> scope. deploy/governance/scopes.json mirrors this and a test keeps them equal.
TOOL_SCOPE: dict[str, str] = {
    "get_my_quota_attainment": "read",
    "list_my_accounts": "read",
    "get_account_insights": "read",
    "list_my_opportunities": "read",
    "get_my_pipeline_summary": "read",
    "get_my_commission_estimate": "read",
    "list_sales_reps": "team",
    "get_rep_quota_attainment": "team",
    "list_rep_opportunities": "team",
    "get_rep_compensation": "team",
    "get_team_leaderboard": "team",
    "search_accounts": "team",
    "update_opportunity": "write",
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
    user_id: str | None
    call_id: str
    agent: str
    channel: str


def caller_from(ctx: Context) -> Caller:
    req = ctx.request_context.request
    headers = req.headers if req is not None else {}
    key = headers.get("x-api-key", "")
    channel = KEYS.get(key, "unknown" if key else "none")
    return Caller(
        user_id=(headers.get("x-acting-user") or None),
        call_id=headers.get("x-call-id") or uuid.uuid4().hex[:12],
        agent=headers.get("x-agent-name") or "unknown",
        channel=channel,
    )


def _record(
    ctx: Context,
    tool: str,
    args: dict[str, Any],
    *,
    owners: list[str],
    sensitivity: str,
    summary: str,
    sensitive_fields: int = 0,
    mutation: bool = False,
    outcome: str = "ok",
) -> None:
    caller = caller_from(ctx)
    owners = sorted(set(owners))
    cross = bool(owners) and any(o != caller.user_id for o in owners)
    if outcome != "ok":
        verdict = outcome
    elif mutation:
        verdict = "leak" if cross else "write"
    elif cross and sensitivity in ("confidential", "restricted"):
        verdict = "leak"
    elif cross:
        verdict = "exposure"
    else:
        verdict = "ok"
    actor = data.REPS.get(caller.user_id or "")
    AUDIT.add(
        call_id=caller.call_id,
        tool=tool,
        scope=f"salesforce:{TOOL_SCOPE[tool]}",
        args={k: v for k, v in args.items() if v is not None},
        acting_user=caller.user_id,
        acting_name=actor["name"] if actor else None,
        agent=caller.agent,
        channel=caller.channel,
        data_owners=owners,
        owner_names=[data.REPS[o]["name"] for o in owners if o in data.REPS],
        cross_owner=cross,
        sensitivity=sensitivity,
        sensitive_fields=sensitive_fields if verdict in ("leak", "exposure") else 0,
        mutation=mutation,
        verdict=verdict,
        summary=summary,
    )


def _me(ctx: Context, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    caller = caller_from(ctx)
    rep = data.REPS.get(caller.user_id or "")
    if not rep or not rep["annual_quota"]:
        _record(ctx, tool, args or {}, owners=[], sensitivity="internal", outcome="denied",
                summary="Rejected: no signed-in account manager on this request")
        raise ValueError("No signed-in account manager on this request (X-Acting-User is missing or unknown).")
    return rep


def _rep(ctx: Context, tool: str, ref: str, args: dict[str, Any]) -> dict[str, Any]:
    rep = data.find_rep(ref)
    if not rep:
        _record(ctx, tool, args, owners=[], sensitivity="internal", outcome="not_found",
                summary=f"Rep '{ref}' not found")
        raise ValueError(f"No sales rep matches '{ref}'. Use list_sales_reps to see valid ids.")
    return rep


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Salesforce (mock)",
    instructions=(
        "Mock Salesforce CRM for sales quota, accounts and pipeline. Tools named get_my_* and list_my_* "
        "return the signed-in account manager's own data. Other tools work across the whole sales team."
    ),
    host="0.0.0.0",
    port=PORT,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ----- own-book tools (scope: salesforce:read) ---------------------------------------------------


@mcp.tool()
def get_my_quota_attainment(ctx: Context) -> dict[str, Any]:
    """Quota attainment for the signed-in account manager: annual quota, closed-won to date,
    attainment percent, gap to quota, quarter by quarter results, pipeline coverage and forecast."""
    rep = _me(ctx, "get_my_quota_attainment")
    out = data.quota_summary(rep)
    _record(ctx, "get_my_quota_attainment", {}, owners=[rep["id"]], sensitivity="internal",
            summary=f"Own quota attainment ({out['attainment_pct']}%)")
    return out


@mcp.tool()
def list_my_accounts(ctx: Context, tier: str | None = None) -> dict[str, Any]:
    """List the signed-in account manager's accounts with ARR, health score, renewal date,
    open pipeline and whitespace. Optional tier filter: Strategic, Enterprise or Mid-Market."""
    rep = _me(ctx, "list_my_accounts", {"tier": tier})
    rows = []
    for a in data.accounts_for(rep["id"]):
        if tier and a["tier"].lower() != tier.lower():
            continue
        open_opps = [o for o in data.opps_for(rep["id"]) if o["account_id"] == a["id"] and o["stage"] in data.OPEN_STAGES]
        rows.append({
            "id": a["id"], "name": a["name"], "industry": a["industry"], "tier": a["tier"], "arr": a["arr"],
            "health_score": a["health_score"], "renewal_date": a["renewal_date"],
            "open_pipeline": sum(o["amount"] for o in open_opps),
            "whitespace_value": sum(w["est_value"] for w in a["whitespace"]),
        })
    rows.sort(key=lambda r: -r["arr"])
    _record(ctx, "list_my_accounts", {"tier": tier}, owners=[rep["id"]], sensitivity="internal",
            summary=f"Listed {len(rows)} of own accounts")
    return {"as_of": data.AS_OF, "count": len(rows), "accounts": rows}


@mcp.tool()
def get_account_insights(ctx: Context, account_id: str) -> dict[str, Any]:
    """Deep dive on one of the signed-in account manager's accounts: products owned, whitespace,
    growth signals, risks, key contacts, recent activity and the latest call notes.
    Only works for accounts the signed-in account manager owns."""
    rep = _me(ctx, "get_account_insights", {"account_id": account_id})
    acc = data.ACCOUNTS.get(account_id.strip()) or next(
        (a for a in data.ACCOUNTS.values() if a["name"].lower() == account_id.strip().lower()), None)
    if not acc or acc["owner_id"] != rep["id"]:
        _record(ctx, "get_account_insights", {"account_id": account_id}, owners=[], sensitivity="internal",
                outcome="not_found", summary=f"Account '{account_id}' not visible to this user")
        raise ValueError(f"Account '{account_id}' was not found in your book of business.")
    opps = [data.public_opp(o) for o in data.opps_for(rep["id"]) if o["account_id"] == acc["id"]]
    _record(ctx, "get_account_insights", {"account_id": account_id}, owners=[rep["id"]], sensitivity="internal",
            summary=f"Account insights for {acc['name']}")
    return {
        "as_of": data.AS_OF,
        "account": {k: acc[k] for k in ("id", "name", "industry", "tier", "arr", "health_score", "renewal_date", "products_owned")},
        "whitespace": acc["whitespace"],
        "growth_signals": acc["signals"],
        "risks": acc["risks"],
        "contacts": acc["contacts"],
        "recent_activity": acc["recent_activity"],
        "latest_call_notes": acc["latest_call_notes"],
        "opportunities": opps,
    }


@mcp.tool()
def list_my_opportunities(ctx: Context, stage: str | None = None, open_only: bool = True) -> dict[str, Any]:
    """List the signed-in account manager's opportunities with amount, stage, close date, probability,
    next step and discount. Set open_only false to include closed deals. Optional stage filter."""
    rep = _me(ctx, "list_my_opportunities", {"stage": stage, "open_only": open_only})
    rows = []
    for o in data.opps_for(rep["id"]):
        if open_only and o["stage"] not in data.OPEN_STAGES:
            continue
        if stage and o["stage"].lower() != stage.lower():
            continue
        rows.append(data.public_opp(o))
    rows.sort(key=lambda o: (o["close_date"], -o["amount"]))
    _record(ctx, "list_my_opportunities", {"stage": stage, "open_only": open_only}, owners=[rep["id"]],
            sensitivity="internal", summary=f"Listed {len(rows)} own opportunities")
    return {"as_of": data.AS_OF, "count": len(rows), "opportunities": rows}


@mcp.tool()
def get_my_pipeline_summary(ctx: Context) -> dict[str, Any]:
    """Pipeline summary for the signed-in account manager by stage: count, amount and probability
    weighted amount, plus what is expected to close before the fiscal year ends."""
    rep = _me(ctx, "get_my_pipeline_summary")
    q = data.quota_summary(rep)
    _record(ctx, "get_my_pipeline_summary", {}, owners=[rep["id"]], sensitivity="internal",
            summary="Own pipeline summary")
    return {
        "as_of": data.AS_OF,
        "by_stage": data.pipeline_by_stage(rep["id"]),
        "open_pipeline_total": q["open_pipeline_total"],
        "closing_this_fiscal_year": q["open_pipeline_closing_this_fy"],
        "weighted_closing_this_fiscal_year": q["weighted_pipeline_this_fy"],
        "remaining_to_quota": q["remaining_to_quota"],
        "pipeline_coverage_x": q["pipeline_coverage_x"],
    }


@mcp.tool()
def get_my_commission_estimate(ctx: Context, attainment_pct: float | None = None) -> dict[str, Any]:
    """Commission estimate for the signed-in account manager: earned to date and projected at 100 percent
    and at a chosen attainment percent. Uses the public commission plan (base rate plus accelerator above quota)."""
    rep = _me(ctx, "get_my_commission_estimate", {"attainment_pct": attainment_pct})
    q = data.quota_summary(rep)
    quota = rep["annual_quota"]
    scenario = attainment_pct if attainment_pct is not None else q["forecast_attainment_pct"]
    _record(ctx, "get_my_commission_estimate", {"attainment_pct": attainment_pct}, owners=[rep["id"]],
            sensitivity="internal", summary="Own commission estimate")
    return {
        "as_of": data.AS_OF,
        "plan": {"commission_rate": rep["commission_rate"], "accelerator_rate_above_quota": rep["accelerator_rate"]},
        "earned_to_date": data.commission(rep, q["closed_won_ytd"]),
        "projected_at_100_pct": data.commission(rep, quota),
        "scenario_attainment_pct": scenario,
        "projected_at_scenario": data.commission(rep, quota * scenario / 100),
    }


# ----- team tools (scope: salesforce:team) -------------------------------------------------------


@mcp.tool()
def list_sales_reps(ctx: Context) -> dict[str, Any]:
    """List every sales rep and manager in the company with id, name, title, region and manager."""
    reps = [{k: r[k] for k in ("id", "name", "title", "region", "manager_id")} for r in data.REPS.values()]
    _record(ctx, "list_sales_reps", {}, owners=[r["id"] for r in reps], sensitivity="internal",
            summary=f"Listed all {len(reps)} sales reps")
    return {"reps": reps}


@mcp.tool()
def get_rep_quota_attainment(ctx: Context, rep_id: str) -> dict[str, Any]:
    """Quota attainment for any sales rep. rep_id is an id such as AM-102 or the rep's name."""
    rep = _rep(ctx, "get_rep_quota_attainment", rep_id, {"rep_id": rep_id})
    if not rep["annual_quota"]:
        raise ValueError(f"{rep['name']} does not carry a quota.")
    out = data.quota_summary(rep)
    _record(ctx, "get_rep_quota_attainment", {"rep_id": rep_id}, owners=[rep["id"]], sensitivity="confidential",
            sensitive_fields=4, summary=f"Quota attainment for {rep['name']} ({out['attainment_pct']}%)")
    return out


@mcp.tool()
def list_rep_opportunities(ctx: Context, rep_id: str, open_only: bool = True) -> dict[str, Any]:
    """List any sales rep's opportunities with amount, stage, close date, discount and competitor."""
    rep = _rep(ctx, "list_rep_opportunities", rep_id, {"rep_id": rep_id, "open_only": open_only})
    rows = [data.public_opp(o) for o in data.opps_for(rep["id"]) if not open_only or o["stage"] in data.OPEN_STAGES]
    rows.sort(key=lambda o: -o["amount"])
    _record(ctx, "list_rep_opportunities", {"rep_id": rep_id, "open_only": open_only}, owners=[rep["id"]],
            sensitivity="confidential", sensitive_fields=max(2, 3 * len(rows)),
            summary=f"{len(rows)} deals with discounts and competitors for {rep['name']}")
    return {"rep": rep["name"], "count": len(rows), "opportunities": rows}


@mcp.tool()
def get_rep_compensation(ctx: Context, rep_id: str) -> dict[str, Any]:
    """Compensation details for any sales rep: base salary, commission earned, retention bonus and HR notes."""
    rep = _rep(ctx, "get_rep_compensation", rep_id, {"rep_id": rep_id})
    q = data.quota_summary(rep) if rep["annual_quota"] else None
    earned = data.commission(rep, q["closed_won_ytd"]) if q else 0
    _record(ctx, "get_rep_compensation", {"rep_id": rep_id}, owners=[rep["id"]], sensitivity="restricted",
            sensitive_fields=5, summary=f"Compensation and HR notes for {rep['name']}")
    return {
        "rep": rep["name"], "base_salary": rep["base_salary"], "commission_rate": rep["commission_rate"],
        "commission_earned_ytd": earned, "retention_bonus": rep["retention_bonus"], "hr_notes": rep["hr_notes"],
    }


@mcp.tool()
def get_team_leaderboard(ctx: Context) -> dict[str, Any]:
    """Team leaderboard: every account manager's quota, attainment and commission earned, ranked."""
    rows = []
    for rid in data.AM_IDS:
        rep = data.REPS[rid]
        q = data.quota_summary(rep)
        rows.append({
            "rep_id": rid, "rep": rep["name"], "region": rep["region"], "annual_quota": q["annual_quota"],
            "closed_won_ytd": q["closed_won_ytd"], "attainment_pct": q["attainment_pct"],
            "commission_earned_ytd": data.commission(rep, q["closed_won_ytd"]),
        })
    rows.sort(key=lambda r: -r["attainment_pct"])
    _record(ctx, "get_team_leaderboard", {}, owners=data.AM_IDS, sensitivity="restricted",
            sensitive_fields=4 * len(rows), summary=f"Full team leaderboard with compensation ({len(rows)} reps)")
    return {"as_of": data.AS_OF, "leaderboard": rows}


@mcp.tool()
def search_accounts(ctx: Context, query: str) -> dict[str, Any]:
    """Search all accounts in the company by name or industry. Returns owner, ARR, health, renewal date and contacts."""
    q = query.strip().lower()
    hits = [a for a in data.ACCOUNTS.values() if q in a["name"].lower() or q in a["industry"].lower()][:10]
    rows = [{
        "id": a["id"], "name": a["name"], "owner": data.REPS[a["owner_id"]]["name"], "industry": a["industry"],
        "arr": a["arr"], "health_score": a["health_score"], "renewal_date": a["renewal_date"], "contacts": a["contacts"],
    } for a in hits]
    _record(ctx, "search_accounts", {"query": query}, owners=[a["owner_id"] for a in hits], sensitivity="confidential",
            sensitive_fields=sum(1 + 2 * len(a["contacts"]) for a in hits),
            summary=f"{len(rows)} accounts across the company, with customer contacts")
    return {"count": len(rows), "accounts": rows}


# ----- write tool (scope: salesforce:write) ------------------------------------------------------


@mcp.tool()
def update_opportunity(
    ctx: Context,
    opportunity_id: str,
    stage: str | None = None,
    amount: float | None = None,
    close_date: str | None = None,
    next_step: str | None = None,
) -> dict[str, Any]:
    """Update an opportunity: stage, amount, close date (YYYY-MM-DD) or next step. Works on any opportunity."""
    args = {"opportunity_id": opportunity_id, "stage": stage, "amount": amount, "close_date": close_date, "next_step": next_step}
    opp = data.OPPORTUNITIES.get(opportunity_id.strip())
    if not opp:
        _record(ctx, "update_opportunity", args, owners=[], sensitivity="internal", outcome="not_found",
                summary=f"Opportunity '{opportunity_id}' not found")
        raise ValueError(f"Opportunity '{opportunity_id}' not found.")
    if stage and stage not in data.STAGES:
        raise ValueError(f"stage must be one of {', '.join(data.STAGES)}")
    before = {k: opp[k] for k in ("stage", "amount", "close_date", "next_step")}
    if stage:
        opp["stage"] = stage
        opp["probability"] = 100 if stage == "Closed Won" else 0 if stage == "Closed Lost" else opp["probability"]
    if amount is not None:
        opp["amount"] = amount
    if close_date:
        opp["close_date"] = close_date
    if next_step:
        opp["next_step"] = next_step
    after = {k: opp[k] for k in before}
    changed = ", ".join(f"{k} {before[k]} to {after[k]}" for k in before if before[k] != after[k]) or "no change"
    _record(ctx, "update_opportunity", args, owners=[opp["owner_id"]], sensitivity="restricted", mutation=True,
            sensitive_fields=1, summary=f"CRM WRITE on {opp['name']}: {changed}")
    return {"opportunity_id": opp["id"], "name": opp["name"], "before": before, "after": after}


# ---------------------------------------------------------------------------
# Plain HTTP routes
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "as_of": data.AS_OF, "tools": len(TOOL_SCOPE)})


@mcp.custom_route("/audit", methods=["GET"])
async def audit(request: Request) -> JSONResponse:
    try:
        since = int(request.query_params.get("since", "0"))
    except ValueError:
        since = 0
    return JSONResponse({"epoch": AUDIT.epoch, "last_seq": AUDIT.last_seq(), "items": AUDIT.since(since)})


@mcp.custom_route("/catalog", methods=["GET"])
async def catalog(_: Request) -> JSONResponse:
    return JSONResponse({"server": "salesforce-mcp", "scopes": {
        s: sorted(t for t, sc in TOOL_SCOPE.items() if sc == s) for s in ("read", "team", "write")}})


@mcp.custom_route("/admin/reset", methods=["POST"])
async def reset(_: Request) -> JSONResponse:
    data.reset()
    AUDIT.clear()
    return JSONResponse({"ok": True, "epoch": AUDIT.epoch})


# ---------------------------------------------------------------------------
# ASGI app: API-key check on /mcp, open CORS for the demo dashboard
# ---------------------------------------------------------------------------


class ApiKeyAuth:
    """Stands in for the Salesforce integration-user credential."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and AUTH_REQUIRED and scope["path"].rstrip("/") == "/mcp" and scope["method"] != "OPTIONS":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if headers.get("x-api-key", "") not in KEYS:
                AUDIT.add(call_id=headers.get("x-call-id", ""), tool="(connect)", scope="", args={},
                          acting_user=headers.get("x-acting-user"), acting_name=None,
                          agent=headers.get("x-agent-name", "unknown"), channel="none", data_owners=[], owner_names=[],
                          cross_owner=False, sensitivity="internal", sensitive_fields=0, mutation=False,
                          verdict="auth_failed", summary="Rejected: missing or invalid Salesforce credential")
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


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
