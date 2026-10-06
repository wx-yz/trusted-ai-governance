"""Governance telemetry for one chat turn.

The agent reports what it observed while handling a request: which tool calls were allowed or denied by
the gateway, and whether the AI gateway guardrails stopped an LLM request. The demo console turns these
into dashboard events. This is observation only. Enforcement happens in the gateway, not here.
"""

from __future__ import annotations

import time
from typing import Any


class TurnRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.tool_results = 0  # number of tool results already fed back to the model in this turn

    def add(self, type_: str, **fields: Any) -> None:
        self.events.append({"type": type_, "ts": time.time(), **fields})


def guardrail_info(exc: Exception) -> dict[str, Any]:
    """Pull the guardrail name, direction and reason out of the AI gateway's 422 response body."""
    body: Any = getattr(exc, "body", None)
    if not isinstance(body, dict):
        try:
            body = exc.response.json()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            body = {}
    message = body.get("message") if isinstance(body, dict) else None
    detail = message if isinstance(message, dict) else {}
    return {
        "guardrail": (body.get("type") if isinstance(body, dict) else None) or detail.get("interveningGuardrail") or "GUARDRAIL",
        "direction": detail.get("direction", "REQUEST"),
        "reason": detail.get("actionReason") or (message if isinstance(message, str) else "Blocked by gateway policy"),
    }
