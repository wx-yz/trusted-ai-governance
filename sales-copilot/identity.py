"""How the agent proves who it is to the Salesforce MCP server.

AgentID mode follows the Agent Manager guide: mint an OAuth 2.0 client_credentials token with the
injected credential, scoped to the MCP proxy with the RFC 8707 "resource" parameter, cache it per
resource, and send it as a bearer token to the gateway. The token only carries the scopes of the roles
assigned to this agent, which is what makes least privilege enforceable outside the agent.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx

from config import Config


class IdentityNotReady(Exception):
    pass


def decode_claims(token: str) -> dict[str, Any]:
    """Read the claims of a JWT without verifying it. For display only, the gateway does the verifying."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def scopes_from_claims(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw, str):
        return sorted(raw.split())
    if isinstance(raw, list):
        return sorted(str(s) for s in raw)
    return []


class AgentIdentity:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self.granted_scopes: list[str] = []

    async def auth_headers(self, resource: str) -> dict[str, str]:
        cfg = self.cfg
        if cfg.sf_mcp_auth == "apikey":
            return {"X-API-Key": cfg.sf_mcp_api_key}
        if cfg.sf_mcp_auth == "agentid":
            return {"Authorization": f"Bearer {await self._token(resource)}"}
        return {}

    async def _token(self, resource: str) -> str:
        cfg = self.cfg
        async with self._lock:  # one refresh at a time, everyone else reuses the result
            cached = self._tokens.get(resource)
            if cached and time.time() < cached[1]:
                return cached[0]
            if not (cfg.agentid_client_id and cfg.agentid_client_secret and cfg.agentid_token_endpoint):
                raise IdentityNotReady("AgentID has not finished provisioning for this environment yet")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    cfg.agentid_token_endpoint,
                    auth=(cfg.agentid_client_id, cfg.agentid_client_secret),
                    data={"grant_type": "client_credentials", "scope": cfg.agentid_scopes, "resource": resource},
                )
            resp.raise_for_status()
            body = resp.json()
            token = body["access_token"]
            # Refresh at 75% of the lifetime so a slow refresh never leaves a gap.
            self._tokens[resource] = (token, time.time() + float(body.get("expires_in", 3600)) * 0.75)
            self.granted_scopes = scopes_from_claims(decode_claims(token)) or sorted(str(body.get("scope", "")).split())
            return token

    def describe(self) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.sf_mcp_auth == "agentid":
            return {
                "type": "AgentID",
                "client_id": cfg.agentid_client_id,
                "granted_scopes": self.granted_scopes,
                "summary": "Own OAuth identity, scopes filtered by assigned roles",
            }
        if cfg.sf_mcp_auth == "apikey":
            return {
                "type": "shared-service-account",
                "client_id": None,
                "granted_scopes": ["full access"],
                "summary": "Shared Salesforce integration key held by the agent",
            }
        return {"type": "none", "client_id": None, "granted_scopes": [], "summary": "No credential"}
