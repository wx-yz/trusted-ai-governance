"""`/diagnose`: a self-check the agent runs on request, so a failure in Try It names its own cause.

Send the message /diagnose to the agent (Try It works). It reports the configuration it sees (secrets never
printed), then tests, step by step, DNS, TCP, the MCP handshake, the tool list, the AgentID token and a one-token
model call. The first step that fails is the place to look.
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from urllib.parse import urlparse

import httpx
import openai

from config import Config
from identity import AgentIdentity
from mcp_client import SalesforceMcp, classify, explain

TRIGGERS = {"/diagnose", "diagnose", "/diag"}


def wants_diagnosis(message: str) -> bool:
    return message.strip().lower() in TRIGGERS


async def run(cfg: Config, identity: AgentIdentity, mcp: SalesforceMcp, build_llm) -> str:
    rows: list[str] = []

    def ok(name: str, detail: str = "") -> None:
        rows.append(f"- ✅ **{name}** {detail}".rstrip())

    def bad(name: str, detail: str, hint: str = "") -> None:
        rows.append(f"- ❌ **{name}** {detail}" + (f"\n  - {hint}" if hint else ""))

    def info(name: str, detail: str) -> None:
        rows.append(f"- ℹ️ **{name}** {detail}")

    info("Agent", f"`{cfg.agent_name}` v{cfg.agent_version}")
    info("LLM path", f"`{cfg.llm_path}`" + (f" via `{urlparse(cfg.llm_provider_url).netloc}`" if cfg.use_llm_provider else ""))
    if cfg.sf_mcp_auth == "apikey":
        fp = f"yes, starts with `{cfg.sf_mcp_api_key[:4]}…` ({len(cfg.sf_mcp_api_key)} characters)" if cfg.sf_mcp_api_key else "no"
    else:
        fp = "yes" if cfg.agentid_client_id else "no"
    info("Salesforce auth", f"`{cfg.sf_mcp_auth}`, credential present: {fp}")
    info("SF_MCP_URL", f"`{cfg.sf_mcp_url or 'NOT SET'}`")
    info("LLM-related variables present", ", ".join(f"`{k}`" for k in cfg.llm_env_seen) or "none (names only, values are never shown)")
    for problem in cfg.problems():
        bad("Configuration", problem)

    url = urlparse(cfg.sf_mcp_url)
    if url.hostname:
        port = url.port or (443 if url.scheme == "https" else 80)
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(loop.getaddrinfo(url.hostname, port, type=socket.SOCK_STREAM), 5)
            ok("DNS", f"`{url.hostname}` resolves to {', '.join(sorted({i[4][0] for i in infos}))}")
        except Exception as exc:  # noqa: BLE001
            bad("DNS", f"`{url.hostname}` did not resolve ({type(exc).__name__})", explain(None, "getaddrinfo", cfg.sf_mcp_url))
        else:
            try:
                t0 = time.time()
                _, writer = await asyncio.wait_for(asyncio.open_connection(url.hostname, port, ssl=url.scheme == "https" or None), 5)
                writer.close()
                ok("TCP", f"connected to port {port} in {int((time.time() - t0) * 1000)} ms")
            except Exception as exc:  # noqa: BLE001
                bad("TCP", f"could not connect to `{url.hostname}:{port}` ({type(exc).__name__})", explain(None, "connecterror", cfg.sf_mcp_url))

    if cfg.sf_mcp_url:
        try:
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       **await identity.auth_headers(cfg.sf_mcp_url)}
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "diagnose", "version": "1"}}}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(cfg.sf_mcp_url, json=init, headers=headers)
            if resp.status_code == 200:
                ok("MCP handshake", f"HTTP 200, `{resp.headers.get('content-type', '')}`")
            else:
                bad("MCP handshake", f"HTTP {resp.status_code}: {resp.text[:140]!r}", explain(resp.status_code, ""))
        except Exception as exc:  # noqa: BLE001
            status, _, msg = classify(exc)
            bad("MCP handshake", msg, explain(status, msg, cfg.sf_mcp_url))
        try:
            tools = await mcp.list_tools(None, uuid.uuid4().hex[:12])
            ok("Tool list", f"{len(tools)} tools visible")
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            status, _, msg = classify(exc)
            bad("Tool list", msg, explain(status, msg, cfg.sf_mcp_url))

    if cfg.sf_mcp_auth == "agentid":
        try:
            await identity.auth_headers(cfg.sf_mcp_url)
            ok("AgentID token", "minted, scopes: " + (", ".join(identity.granted_scopes) or "none"))
        except Exception as exc:  # noqa: BLE001
            status, _, msg = classify(exc)
            bad("AgentID token", msg, "Check the role assignment and that the agent finished provisioning.")

    if not any(p for p in cfg.problems() if "OPENAI" in p or "LLM_PROVIDER" in p):
        try:
            await build_llm(cfg).bind(max_tokens=1).ainvoke("ping")
            ok("Model call", "answered")
        except openai.APIStatusError as exc:
            from agent import explain_llm  # local import: agent imports this module
            bad("Model call", f"HTTP {exc.status_code}: {str(exc.body)[:160]}", explain_llm(exc.status_code, str(exc.body), cfg))
        except Exception as exc:  # noqa: BLE001
            bad("Model call", f"{type(exc).__name__}: {str(exc)[:140]}")

    failing = sum(1 for r in rows if "❌" in r)
    head = "### Diagnosis: everything checked passed" if not failing else f"### Diagnosis: {failing} problem(s) found"
    return head + "\n" + "\n".join(rows)
