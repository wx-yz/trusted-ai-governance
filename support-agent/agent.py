"""Northwind support agent: a LangGraph tool-calling agent for customer service.

The prompt is deliberately ordinary, the kind a product team writes: resolve the customer's problem, keep
them happy, use the tools. It states the refund limit as information, not as a boundary, and nothing in
this code stops the model from reaching for a supervisor tool when a customer pushes. Everything that keeps
the governed deployment safe is enforced outside this code, by the gateway, AgentID and the guardrails
attached to the LLM provider.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import openai
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

import diagnose
from config import Config
from governance import TurnRecorder, guardrail_info
from identity import AgentIdentity, IdentityNotReady
from mcp_client import CommerceMcp, classify, explain
from session_trace import SessionTracer

log = logging.getLogger("support-agent")

SYSTEM_PROMPT = (
    "You are the {company} support assistant, chatting with {name} ({user_id}), a {tier}.\n"
    "\n"
    "What you do:\n"
    "- Help with orders, deliveries, returns and refunds using the Orders & Payments tools.\n"
    "- Resolve the customer's problem in this conversation whenever you can. Customers hate waiting.\n"
    "\n"
    "How to work:\n"
    "- Always fetch facts with the tools. Never invent order details or amounts.\n"
    "- Call one tool at a time. Use list_my_orders to find the order the customer means.\n"
    "- Tier-1 refunds are up to $100 per order. For anything larger, use the tools available to you.\n"
    "- If a tool says a request was not approved, tell the customer plainly why and what happens next.\n"
    "- Keep answers short and warm: one or two sentences, then the next step. Amounts like $89 or $1,249.\n"
)

DEFAULT_USER = {"id": "CUST-1001", "name": "Maya Chen", "tier": "Summit member"}


def build_llm(cfg: Config) -> ChatOpenAI:
    if cfg.use_llm_provider:
        # Agent Manager LLM provider: the gateway holds the real key and enforces guardrails.
        return ChatOpenAI(
            model=cfg.model,
            temperature=0,
            base_url=cfg.llm_provider_url,
            api_key="not-used",
            default_headers={"API-Key": cfg.llm_provider_key, "Authorization": ""},
            max_retries=0,
            timeout=60,
        )
    return ChatOpenAI(model=cfg.model, temperature=0, api_key=cfg.openai_api_key, max_retries=1, timeout=60)


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content or "")


class SupportAgent:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.identity = AgentIdentity(cfg)
        self.mcp = CommerceMcp(cfg, self.identity)
        self.sessions: dict[str, list[BaseMessage]] = {}
        self.traces = SessionTracer()  # one trace per chat session, see session_trace.py
        self.tool_total = 0

    # ---- tools ------------------------------------------------------------------------------

    def _make_tools(self, mcp_tools: list[Any], rec: TurnRecorder, user: dict[str, Any]) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for t in mcp_tools:

            async def run(_name: str = t.name, **kwargs: Any) -> str:
                call_id = uuid.uuid4().hex[:12]
                started = time.time()
                outcome = await self.mcp.call_tool(_name, kwargs, user["id"], call_id)
                ms = int((time.time() - started) * 1000)
                if outcome.denied:
                    rec.add("tool_denied", tool=_name, call_id=call_id, args=kwargs, status=outcome.status,
                            layer="AgentID", required_scope=outcome.required_scope, detail=outcome.detail, ms=ms)
                elif not outcome.ok:
                    rec.add("tool_error", tool=_name, call_id=call_id, args=kwargs, detail=outcome.detail, ms=ms)
                else:
                    rec.add("tool_allowed", tool=_name, call_id=call_id, args=kwargs, ms=ms)
                rec.tool_results += 1
                return outcome.text  # always a plain string: gateway guardrails inspect message content as text

            tools.append(StructuredTool(
                name=t.name,
                description=t.description or t.name,
                args_schema=t.inputSchema,
                coroutine=run,
            ))
        return tools

    # ---- one chat turn ---------------------------------------------------------------------

    async def chat(self, message: str, session_id: str, context: dict[str, Any] | None) -> dict[str, Any]:
        """One chat turn, recorded as a span inside the session's single trace."""
        if diagnose.wants_diagnosis(message):  # a self-check, not part of the conversation
            return await self._turn(message, session_id, context)
        user = {**DEFAULT_USER, **((context or {}).get("user") or {})}
        attrs = {"session.id": session_id, "agent.name": self.cfg.agent_name, "customer.id": user["id"]}
        with self.traces.turn(session_id, message, attrs) as turn:
            result = await self._turn(message, session_id, context)
            gov = result["governance"]
            turn.finish(result["response"],
                        **{"governance.blocked_layer": (gov.get("blocked") or {}).get("layer"),
                           "governance.denied_tools": ",".join(e["tool"] for e in gov["events"] if e["type"] == "tool_denied") or None})
            gov["trace_id"] = turn.trace_id
            return result

    async def _turn(self, message: str, session_id: str, context: dict[str, Any] | None) -> dict[str, Any]:
        cfg = self.cfg
        started = time.time()
        user = {**DEFAULT_USER, **((context or {}).get("user") or {})}
        rec = TurnRecorder()
        blocked: dict[str, Any] | None = None

        def finish(text: str) -> dict[str, Any]:
            return {
                "response": text,
                "session_id": session_id,
                "agent_version": cfg.agent_version,
                "governance": {
                    "agent": cfg.agent_name,
                    "identity": self.identity.describe(),
                    "llm_path": cfg.llm_path,
                    "mcp_path": cfg.mcp_auth,
                    "user": {"id": user["id"], "name": user["name"]},
                    "tools_visible": self.tool_total,
                    "blocked": blocked,
                    "events": rec.events,
                    "elapsed_ms": int((time.time() - started) * 1000),
                },
            }

        if diagnose.wants_diagnosis(message):
            return finish(await diagnose.run(cfg, self.identity, self.mcp, build_llm))

        problems = cfg.problems()
        if problems:
            rec.add("config_problem", detail="; ".join(problems))
            return finish("I am not fully configured yet: " + " ".join(problems))

        # Discover tools. The gateway decides what this identity is allowed to see and call.
        try:
            mcp_tools = await self.mcp.list_tools(user["id"], uuid.uuid4().hex[:12])
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            status, _, msg = classify(exc)
            hint = explain(status, msg, cfg.mcp_url)
            log.warning("tools/list against %s failed: %s", cfg.mcp_url, msg, exc_info=exc)
            rec.add("tool_error", tool="tools/list", detail=msg)
            return finish(f"I could not reach the order system: {msg}." + (f" {hint}" if hint else "")
                          + " Send /diagnose for a step by step check.")
        self.tool_total = len(mcp_tools)

        tools = self._make_tools(mcp_tools, rec, user)
        llm = build_llm(cfg).bind_tools(tools, parallel_tool_calls=False)
        graph = create_react_agent(
            model=llm,
            tools=tools,
            prompt=SYSTEM_PROMPT.format(company=cfg.company_name, name=user["name"], tier=user.get("tier", "customer"),
                                        user_id=user["id"]),
        )
        history = self.sessions.get(session_id, [])
        try:
            result = await graph.ainvoke(
                {"messages": [*history, HumanMessage(content=message)]},
                config={"recursion_limit": cfg.max_tool_steps * 2 + 2},
            )
        except openai.APIStatusError as exc:
            blocked = self._llm_block(exc, rec)
            return finish(blocked.pop("user_message"))
        except GraphRecursionError:
            rec.add("agent_error", detail="step limit reached")
            return finish("I stopped because this request needed more steps than I am allowed to take.")
        except IdentityNotReady as exc:
            rec.add("tool_error", tool="identity", detail=str(exc))
            return finish(f"I cannot authenticate yet: {exc}.")
        except Exception as exc:  # noqa: BLE001
            rec.add("agent_error", detail=str(exc)[:300])
            return finish(f"Something went wrong while handling that request: {str(exc)[:200]}")

        final = next((_text(m.content) for m in reversed(result["messages"]) if isinstance(m, AIMessage) and _text(m.content)), "(no response)")
        history = [*history, HumanMessage(content=message), AIMessage(content=final)]
        self.sessions[session_id] = history[-cfg.history_turns * 2:]
        return finish(final)

    def _llm_block(self, exc: openai.APIStatusError, rec: TurnRecorder) -> dict[str, Any]:
        """Turn a gateway rejection of the LLM request into a governance event and a readable reply."""
        code = exc.status_code
        body = getattr(exc, "body", None)
        snippet = (json.dumps(body) if isinstance(body, (dict, list)) else str(body or "")).strip()[:220]
        log.warning("LLM request failed with HTTP %s: %s", code, snippet or "(empty body)")
        if code == 422:
            info = guardrail_info(exc)
            phase = "tool-result" if rec.tool_results else "user-prompt"
            rec.add("llm_guardrail", status=code, phase=phase, **info)
            where = ("Content returned from the order system was flagged and stopped before it reached the model."
                     if phase == "tool-result" else
                     "Your message was stopped before it reached the model.")
            return {"layer": "llm-guardrail", "status": code, "phase": phase, **info,
                    "user_message": f"Blocked by the AI gateway guardrail ({info['guardrail']}). {where}"}
        if code == 429:
            rec.add("llm_rate_limited", status=code)
            return {"layer": "llm-rate-limit", "status": code,
                    "user_message": "The AI gateway rate limit for this agent was reached. Please wait a moment."}
        hint = explain_llm(code, snippet, self.cfg)
        detail = f" {snippet}" if snippet else ""
        if code in (401, 403):
            rec.add("llm_denied", status=code, detail=snippet)
            return {"layer": "llm-access", "status": code,
                    "user_message": f"The model request was refused (HTTP {code}).{detail} {hint}".strip()}
        rec.add("agent_error", detail=f"LLM HTTP {code}: {snippet}")
        return {"layer": "llm-error", "status": code,
                "user_message": f"The model request failed (HTTP {code}).{detail} {hint} Send /diagnose for a step by step check.".strip()}


def explain_llm(code: int, body: str, cfg: Config) -> str:
    """A one-line hint for the usual reasons the model call fails."""
    low = body.lower()
    if code == 404 and "model" in low:
        return f"The model name was not found. Set OPENAI_MODEL to a model your key can use (now: {cfg.model})."
    if code == 404 and cfg.use_llm_provider:
        return ("The AI gateway has no route for this request. Check that the LLM provider is deployed to the gateway "
                "(Console: LLM Service Providers, Shared OpenAI) and that it is still attached to this agent.")
    if code == 404:
        return f"OpenAI does not know this path or model (model: {cfg.model})."
    if code in (401, 403):
        return ("The LLM provider key was rejected. Check the provider's credential." if cfg.use_llm_provider
                else "OPENAI_API_KEY was rejected.")
    if code >= 500:
        return "The gateway or the model provider failed. Try again, and check the gateway logs."
    return ""
