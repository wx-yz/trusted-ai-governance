"""Readable titles for the spans this agent creates, so a trace in the Agent Manager console tells the story.

The trace list shows the root span's name and the trace view shows every span's name. The auto-instrumentation
names its spans after code (LangGraph.workflow, execute_tool issue_refund, ChatOpenAI.chat). Those stay as they are:
the platform and the refund-policy evaluator read them. The spans named here are the ones this agent owns:

    Support chat · Maya Chen (CUST-1001) · "My $340 jacket doesn't fit…"                 trace root
      Turn 1 · payments refused $340 refund · escalated $340 to a supervisor · "My $340…"  one per chat turn
        Discover Orders & Payments tools → 8 visible
        issue_refund ORD-1031 $340.00 → refused by payments: $340 exceeds the $100 …       inside execute_tool
        approve_exception_refund ORD-1031 $340.00 → blocked at gateway (HTTP 403, needs commerce:approve)
"""

from __future__ import annotations

import json
from typing import Any

MONEY_TOOLS = {"issue_refund", "approve_exception_refund", "issue_store_credit"}
_KIND = {"issue_refund": "refund", "approve_exception_refund": "exception refund", "issue_store_credit": "store credit"}


def clip(text: Any, limit: int = 60) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def quote(text: Any, limit: int = 60) -> str:
    return f'"{clip(text, limit)}"'


def usd(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return ""
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"


def _amount(args: dict[str, Any]) -> Any:
    return args.get("amount", args.get("requested_amount"))


def tool_verdict(tool: str, text: str) -> tuple[str, str]:
    """What the payments system said about an allowed call: (approved | refused | escalated | ok, reason)."""
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return "ok", ""
    if not isinstance(body, dict):
        return "ok", ""
    if body.get("escalated"):
        return "escalated", str(body.get("case_id") or "")
    if body.get("approved") is False:
        return "refused", str(body.get("reason") or "")
    if body.get("approved") is True:
        return "approved", ""
    return "ok", ""


def tool_title(tool: str, args: dict[str, Any], *, verdict: str = "ok", reason: str = "", denied: bool = False,
               status: int | None = None, scope: str | None = None, error: str | None = None) -> str:
    """Title for one Orders & Payments call, with its outcome."""
    target = args.get("order_id") or args.get("case_id") or ""
    amt = _amount(args)
    base = " ".join(p for p in (tool, str(target), usd(amt) if amt is not None else "") if p)
    if denied:
        return f"{base} → blocked at gateway (HTTP {status or 403}{', needs ' + scope if scope else ''})"
    if error is not None:
        return f"{base} → error: {clip(error, 60)}"
    if verdict == "refused":
        return f"{base} → refused by payments: {clip(reason, 70)}"
    if verdict == "escalated":
        return f"{base} → {'case ' + reason + ' ' if reason else ''}opened for a supervisor"
    if verdict == "approved" and tool == "issue_refund":
        return f"{base} → refund issued within policy"
    if verdict == "approved" and tool == "approve_exception_refund":
        return f"{base} → exception approved by the agent, no human"
    if verdict == "approved" and tool == "issue_store_credit":
        return f"{base} → store credit granted by the agent, no human"
    return f"{base} → ok"


def turn_outcome(events: list[dict[str, Any]], max_parts: int = 4) -> str:
    """One phrase per thing that mattered in a turn, in the order it happened."""
    parts: list[str] = []
    reads: list[str] = []
    for e in events:
        t, tool, a = e.get("type"), e.get("tool", ""), e.get("args") or {}
        amt = usd(_amount(a)) if _amount(a) is not None else ""
        if t == "llm_guardrail":
            where = " on a tool result" if e.get("phase") == "tool-result" else ""
            parts.append(f"guardrail {e.get('guardrail') or 'GUARDRAIL'} blocked the model call{where}")
        elif t == "tool_denied":
            parts.append(f"gateway denied {tool}")
        elif t == "tool_allowed":
            v = e.get("verdict")
            if v == "approved" and tool == "issue_refund":
                parts.append(f"refunded {amt}".strip())
            elif v == "approved" and tool == "approve_exception_refund":
                parts.append(f"self-approved {amt} exception (no human)".replace("  ", " "))
            elif v == "approved" and tool == "issue_store_credit":
                parts.append(f"{amt} store credit (no human)".strip())
            elif v == "refused":
                parts.append(f"payments refused {amt} {_KIND.get(tool, tool)}".replace("  ", " "))
            elif v == "escalated":
                parts.append(f"escalated {amt} to a supervisor".replace("  ", " ") if amt else "escalated to a supervisor")
            elif tool not in reads:
                reads.append(tool)
        elif t in ("tool_error", "agent_error", "config_problem", "llm_denied", "llm_rate_limited"):
            detail = e.get("detail") or (f"HTTP {e['status']}" if e.get("status") else "")
            parts.append(f"{t.replace('_', ' ')}: {clip(detail, 50)}" if detail else t.replace("_", " "))
    seen: set[str] = set()
    parts = [p for p in parts if not (p in seen or seen.add(p))]
    if not parts:
        return "answered using " + ", ".join(reads[:3]) if reads else "answered"
    if len(parts) > max_parts:
        parts = parts[:max_parts] + [f"+{len(parts) - max_parts} more"]
    return " · ".join(parts)


def turn_title(n: int, message: str, outcome: str | None = None) -> str:
    return f"Turn {n} · {outcome} · {quote(message, 50)}" if outcome else f"Turn {n} · {quote(message, 60)}"


def session_title(customer: str | None, message: str) -> str:
    return f"Support chat · {customer} · {quote(message, 60)}" if customer else f"Support chat · {quote(message, 60)}"
