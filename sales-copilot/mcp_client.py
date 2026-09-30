"""Salesforce MCP client used by the agent.

A new MCP session is opened per call. That keeps the code stateless and means the identity headers
(signed-in user, call id, fresh token) are always current. Denials from the gateway are classified so
the agent can report them as governance events instead of treating them as generic failures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from config import Config
from identity import AgentIdentity, IdentityNotReady


@dataclass
class ToolOutcome:
    text: str
    ok: bool = True
    denied: bool = False
    status: int | None = None
    required_scope: str | None = None
    detail: str | None = None


# A gateway normally denies with HTTP 403, but some paths report the same thing as a JSON-RPC error.
_DENIED = re.compile(r"(?i)\b(forbidden|insufficient[_ ]scope|insufficient permissions|not authori[sz]ed|access denied|permission denied)\b")


def _walk(exc: BaseException):
    """Yield an exception and everything nested in it (exception groups, causes, contexts)."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        stack.extend(getattr(e, "exceptions", None) or [])
        for nested in (e.__cause__, e.__context__):
            if nested is not None:
                stack.append(nested)


def classify(exc: BaseException) -> tuple[int | None, str | None, str]:
    """Return (http status, required scope, message) for a failed MCP call."""
    status: int | None = None
    scope: str | None = None
    message = ""
    for e in _walk(exc):
        if isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            www = e.response.headers.get("www-authenticate", "")
            m = re.search(r'scope="([^"]+)"', www)
            if m:
                scope = m.group(1)
            message = message or str(e)
        elif isinstance(e, McpError):
            message = message or str(e)
            m = re.search(r"\b(401|403)\b", str(e))
            if m and status is None:
                status = int(m.group(1))
            elif status is None and _DENIED.search(str(e)):
                status = 403
        elif isinstance(e, IdentityNotReady):
            message = str(e)
            status = status or 401
        elif not message and str(e):
            message = str(e)
    if status is None:
        m = re.search(r"\b(401|403)\b", message)
        if m:
            status = int(m.group(1))
        elif _DENIED.search(message):
            status = 403
    return status, scope, message or exc.__class__.__name__


def _compact(text: str) -> str:
    try:
        return json.dumps(json.loads(text), separators=(",", ":"))
    except (ValueError, TypeError):
        return text


class SalesforceMcp:
    def __init__(self, cfg: Config, identity: AgentIdentity) -> None:
        self.cfg = cfg
        self.identity = identity

    async def _headers(self, user_id: str | None, call_id: str) -> dict[str, str]:
        headers = await self.identity.auth_headers(self.cfg.sf_mcp_url)
        headers["X-Call-Id"] = call_id
        headers["X-Agent-Name"] = self.cfg.agent_name
        if user_id:
            # The signed-in account manager. The model never chooses this, the application does.
            headers["X-Acting-User"] = user_id
        return headers

    async def list_tools(self, user_id: str | None, call_id: str) -> list[types.Tool]:
        headers = await self._headers(user_id, call_id)
        async with streamablehttp_client(self.cfg.sf_mcp_url, headers=headers, timeout=20) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return list((await session.list_tools()).tools)

    async def call_tool(self, name: str, arguments: dict[str, Any], user_id: str | None, call_id: str) -> ToolOutcome:
        try:
            headers = await self._headers(user_id, call_id)
            async with streamablehttp_client(self.cfg.sf_mcp_url, headers=headers, timeout=30) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except BaseException as exc:  # noqa: BLE001  (includes exception groups from the transport)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            status, scope, message = classify(exc)
            if status in (401, 403):
                why = f" (required scope: {scope})" if scope else ""
                return ToolOutcome(
                    text=f"ACCESS DENIED (HTTP {status}): this agent is not authorized to call {name}{why}. "
                         "The request was blocked by the gateway, so no data was returned.",
                    ok=False, denied=True, status=status, required_scope=scope, detail=message,
                )
            return ToolOutcome(text=f"ERROR calling {name}: {message}", ok=False, status=status, detail=message)

        text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
        if result.isError:
            if _DENIED.search(text):
                return ToolOutcome(
                    text=f"ACCESS DENIED (HTTP 403): this agent is not authorized to call {name}. "
                         "The request was blocked by the gateway, so no data was returned.",
                    ok=False, denied=True, status=403, detail=text,
                )
            return ToolOutcome(text=f"ERROR: {text}", ok=False, detail=text)
        return ToolOutcome(text=_compact(text))
