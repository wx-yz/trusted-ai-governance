"""Orders & Payments MCP client used by the agent.

A new MCP session is opened per call. That keeps the code stateless and means the identity headers
(signed-in customer, call id, fresh token) are always current. Denials from the gateway are classified so
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
from mcp.shared._httpx_utils import create_mcp_http_client
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
    layer: str | None = None  # who denied: "gateway" (AgentID tool scopes) or "mcp-server" (the server's own check)
    challenge: dict[str, str] | None = None  # the WWW-Authenticate parameters (RFC 6750 section 3)


_PARAM = re.compile(r'([a-zA-Z_]+)\s*=\s*"((?:[^"\\]|\\.)*)"')


def parse_challenge(header: str) -> dict[str, str]:
    """Parameters of a Bearer WWW-Authenticate challenge: error, scope, resource_metadata, error_description."""
    if not header or not header.strip().lower().startswith("bearer"):
        return {}
    return {k.lower(): v for k, v in _PARAM.findall(header)}


def challenge_of(exc: BaseException) -> dict[str, str]:
    """The authorization challenge of a 401/403, from the WWW-Authenticate header or, when a proxy dropped the
    header, from the same parameters repeated in a JSON body."""
    for e in _leaves(exc):
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403):
            found = parse_challenge(e.response.headers.get("www-authenticate", ""))
            try:
                body = json.loads(e.response.content or b"{}")
            except (ValueError, httpx.ResponseNotRead):
                body = {}
            if isinstance(body, dict):
                for k in ("error", "scope", "resource_metadata", "error_description"):
                    if isinstance(body.get(k), str):
                        found.setdefault(k, body[k])
            return found
    return {}


async def _read_error_body(response: httpx.Response) -> None:
    # The MCP client raises on a 4xx before reading the body; read it first so the challenge in it is available.
    if response.status_code >= 400:
        await response.aread()


def _http_client(headers: dict[str, str] | None = None, timeout: httpx.Timeout | None = None,
                 auth: httpx.Auth | None = None) -> httpx.AsyncClient:
    client = create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)
    client.event_hooks["response"].append(_read_error_body)
    return client


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


def _leaves(exc: BaseException) -> list[BaseException]:
    """Every non-group exception nested in exc, in the order they are found."""
    return [e for e in _walk(exc) if not getattr(e, "exceptions", None)]


def _describe(e: BaseException) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} from {e.request.url}"
    text = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


def classify(exc: BaseException) -> tuple[int | None, str | None, str]:
    """Return (http status, required scope, root cause) for a failed MCP call.

    The MCP client raises an ExceptionGroup ("unhandled errors in a TaskGroup"), which says nothing useful. The real
    cause is nested inside, so look there: an HTTP status first, then a transport error, then anything else.
    """
    status: int | None = None
    scope: str | None = None
    leaves = _leaves(exc)
    for e in leaves:
        if isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            m = re.search(r'scope="([^"]+)"', e.response.headers.get("www-authenticate", ""))
            if m:
                scope = m.group(1)
            break
    pick = (
        next((e for e in leaves if isinstance(e, httpx.HTTPStatusError)), None)
        or next((e for e in leaves if isinstance(e, (httpx.TransportError, OSError))), None)
        or next((e for e in leaves if isinstance(e, (McpError, IdentityNotReady))), None)
        or (leaves[0] if leaves else exc)
    )
    message = _describe(pick)
    if isinstance(pick, IdentityNotReady):
        status = status or 401
    if status is None:
        m = re.search(r"\b(401|403)\b", message)
        if m:
            status = int(m.group(1))
        elif isinstance(pick, McpError) and _DENIED.search(str(pick)):
            status = 403
        elif _DENIED.search(message):
            status = 403
    return status, scope, message


def explain(status: int | None, message: str, url: str = "") -> str:
    """A one-line hint for the most common reasons the Orders & Payments MCP server cannot be reached."""
    low = message.lower()
    dns_failed = any(k in low for k in ("name or service not known", "nodename nor servname", "getaddrinfo", "name resolution"))
    if dns_failed and "trycloudflare.com" in url:
        return ("That is a quick-tunnel name, and it stops resolving when the tunnel stops or restarts. The tunnel URL has "
                "probably changed: take the current one from ./deploy/expose-mcp.sh (it also saves it in deploy/.last-mcp-url) "
                "and update COMMERCE_MCP_URL on this agent, then redeploy. For a URL that never changes, see the README section on a stable URL.")
    if status == 401:
        return "The credential was rejected. For the ungoverned agent, COMMERCE_MCP_API_KEY must equal the MCP server's 'direct' key."
    if status == 404 or "session terminated" in low:
        return "Nothing answers at that path. COMMERCE_MCP_URL should end in /mcp."
    if any(k in low for k in ("name or service not known", "nodename nor servname", "getaddrinfo", "name resolution")):
        return "The host name in COMMERCE_MCP_URL does not resolve from the agent pod."
    if any(k in low for k in ("connecterror", "connecttimeout", "all connection attempts failed", "refused", "timed out", "timeout")):
        return ("The agent pod cannot open a connection to COMMERCE_MCP_URL. If it is the in-cluster service URL, a network policy "
                "probably blocks calls into that namespace: set COMMERCE_MCP_URL to the public tunnel URL. If it already is the "
                "tunnel URL, check that deploy/expose-mcp.sh is still running and that the URL has not changed.")
    return ""


def _compact(text: str) -> str:
    try:
        return json.dumps(json.loads(text), separators=(",", ":"))
    except (ValueError, TypeError):
        return text


class CommerceMcp:
    def __init__(self, cfg: Config, identity: AgentIdentity) -> None:
        self.cfg = cfg
        self.identity = identity

    def _from_resource_server(self, ch: dict[str, str]) -> bool:
        """A 403 challenge comes from the MCP server, not the gateway, when it explains itself (error_description)
        and asks for a scope outside the gateway's tool scopes, the ones this agent's AgentID can be granted."""
        gateway_scopes = set((self.cfg.agentid_scopes or "").split())
        return bool(ch.get("error_description")) and ch.get("scope", "") not in gateway_scopes

    async def _headers(self, user_id: str | None, call_id: str) -> dict[str, str]:
        headers = await self.identity.auth_headers(self.cfg.mcp_url)
        headers["X-Call-Id"] = call_id
        headers["X-Agent-Name"] = self.cfg.agent_name
        if user_id:
            # The signed-in customer. The model never chooses this, the application does.
            headers["X-Customer-Id"] = user_id
        return headers

    async def list_tools(self, user_id: str | None, call_id: str) -> list[types.Tool]:
        headers = await self._headers(user_id, call_id)
        async with streamablehttp_client(self.cfg.mcp_url, headers=headers, timeout=20) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return list((await session.list_tools()).tools)

    async def call_tool(self, name: str, arguments: dict[str, Any], user_id: str | None, call_id: str) -> ToolOutcome:
        try:
            headers = await self._headers(user_id, call_id)
            async with streamablehttp_client(self.cfg.mcp_url, headers=headers, timeout=30,
                                             httpx_client_factory=_http_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except BaseException as exc:  # noqa: BLE001  (includes exception groups from the transport)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            status, scope, message = classify(exc)
            ch = challenge_of(exc)
            if status == 403 and self._from_resource_server(ch):
                # The MCP server itself refused (object-level authorization). Tell the model what a customer may hear.
                return ToolOutcome(
                    text=f"REFUSED BY THE ORDERS & PAYMENTS SERVER (HTTP 403, {ch.get('error', 'insufficient_scope')}, "
                         f"required scope: {ch.get('scope', '?')}): {ch.get('error_description', 'not authorized')} "
                         "This is a privacy rule, not a fault: do not retry and do not try other tools for this record. "
                         "Do not reveal anything about the record. Tell the customer kindly that you can only help with "
                         "their own account, and that the account holder can sign in or contact support themselves.",
                    ok=False, denied=True, status=403, required_scope=ch.get("scope") or scope, detail=message,
                    layer="mcp-server", challenge=ch,
                )
            if status in (401, 403):
                why = f" (required scope: {scope})" if scope else ""
                return ToolOutcome(
                    text=f"ACCESS DENIED (HTTP {status}): this agent is not authorized to call {name}{why}. "
                         "The request was blocked by the gateway, so no data was returned.",
                    ok=False, denied=True, status=status, required_scope=scope, detail=message,
                    layer="gateway", challenge=ch or None,
                )
            return ToolOutcome(text=f"ERROR calling {name}: {message}", ok=False, status=status, detail=message)

        text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
        if result.isError:
            if _DENIED.search(text):
                return ToolOutcome(
                    text=f"ACCESS DENIED (HTTP 403): this agent is not authorized to call {name}. "
                         "The request was blocked by the gateway, so no data was returned.",
                    ok=False, denied=True, status=403, detail=text, layer="gateway",
                )
            return ToolOutcome(text=f"ERROR: {text}", ok=False, detail=text)
        return ToolOutcome(text=_compact(text))
