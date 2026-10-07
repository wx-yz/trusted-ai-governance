"""One trace per chat session.

Without this, every POST /chat starts its own trace, so a five-turn conversation shows up as five unrelated traces.
Here the first turn of a session opens a root span, titled with the customer and their opening message. Every
turn, including the first, is a "Turn N" span under that root, retitled with what happened once the turn ends, and everything the auto-instrumentation records during the turn (LangGraph, the LLM
calls, the MCP tool calls) nests under its turn. A new session_id starts a new trace. The demo UI creates a new
session_id on every page load and on reset.

How the trace looks to Agent Manager:
* The root span ends when the first turn ends (a span is only exported once it has ended, and a session has no
  natural end). Agent Manager takes a trace's input, output and start time from its root span, so the trace list
  shows the session's first exchange and the session's start time.
* Later turns are children of that already-ended root. That is valid OpenTelemetry, and the trace view shows them
  in order.
* Every span carries gen_ai.conversation.id = session_id, the OpenTelemetry GenAI convention for a conversation.
* Titles come from trace_names.py.

When no OpenTelemetry SDK is installed (local tests, or running without amp-instrument), the API returns
non-recording spans and all of this is a no-op.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext

from trace_names import session_title, turn_title

_tracer = trace.get_tracer("support-agent.session")


def _entity_input(message: str) -> str:
    # The Agent Manager observer reads traceloop.entity.input as JSON and shows its "inputs" field.
    return json.dumps({"inputs": message})


def _entity_output(text: str) -> str:
    # ...and traceloop.entity.output as outputs.messages[-1].kwargs.content.
    return json.dumps({"outputs": {"messages": [{"kwargs": {"content": text}}]}})


class _Session:
    def __init__(self, root: SpanContext) -> None:
        self.root = root
        self.turns = 0


class SessionTracer:
    """Keeps the root span context of each live session. Bounded, oldest sessions are forgotten first."""

    def __init__(self, max_sessions: int = 1000) -> None:
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._max = max_sessions

    def trace_id(self, session_id: str) -> str | None:
        s = self._sessions.get(session_id)
        return format(s.root.trace_id, "032x") if s else None

    @contextmanager
    def turn(self, session_id: str, message: str, attributes: dict[str, Any] | None = None,
             customer: str | None = None) -> Iterator["Turn"]:
        """Run one chat turn inside the session's trace. Yields a Turn; call turn.finish(reply) before leaving.

        customer labels the trace root, for example "Maya Chen (CUST-1001)"."""
        attrs = {"gen_ai.conversation.id": session_id, **(attributes or {})}
        session = self._sessions.get(session_id)
        root: Span | None = None
        if session is None:
            # First turn: open the conversation root as a brand-new trace (context=empty, so an HTTP server span or
            # anything else that happens to be current cannot adopt it).
            title = session_title(customer, message)
            root = _tracer.start_span(title, context=otel_context.Context(), attributes={
                **attrs, "traceloop.span.kind": "workflow", "traceloop.entity.name": title,
                "traceloop.entity.input": _entity_input(message),
            })
            session = _Session(root.get_span_context())
            if session.root.is_valid:  # a no-op tracer gives an invalid context; then there is nothing to remember
                self._sessions[session_id] = session
                while len(self._sessions) > self._max:
                    self._sessions.popitem(last=False)
        else:
            self._sessions.move_to_end(session_id)
        session.turns += 1
        n = session.turns
        parent = trace.set_span_in_context(root if root is not None else NonRecordingSpan(session.root))
        title = turn_title(n, message)
        span = _tracer.start_span(title, context=parent, attributes={
            **attrs, "traceloop.span.kind": "workflow", "traceloop.entity.name": title,
            "traceloop.entity.input": _entity_input(message), "turn.number": n,
        })
        token = otel_context.attach(trace.set_span_in_context(span))
        t = Turn(span, root, n, message)
        try:
            yield t
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:200]))
            raise
        finally:
            otel_context.detach(token)
            t.close()


class Turn:
    def __init__(self, span: Span, root: Span | None, number: int = 1, message: str = "") -> None:
        self.span = span
        self.root = root
        self.number = number
        self.message = message
        self._reply: str | None = None

    @property
    def trace_id(self) -> str | None:
        ctx = self.span.get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None

    def finish(self, reply: str, outcome: str | None = None, **attributes: Any) -> None:
        """Record the reply. outcome (see trace_names.turn_outcome) goes into the turn's title."""
        self._reply = reply
        if outcome:
            title = turn_title(self.number, self.message, outcome)
            self.span.update_name(title)
            self.span.set_attribute("traceloop.entity.name", title)
            self.span.set_attribute("turn.outcome", outcome)
        for k, v in attributes.items():
            if v is not None:
                self.span.set_attribute(k, v)

    def close(self) -> None:
        if self._reply is not None:
            self.span.set_attribute("traceloop.entity.output", _entity_output(self._reply))
        self.span.end()
        if self.root is not None:  # the conversation root ends with the first turn, carrying the first exchange
            if self._reply is not None:
                self.root.set_attribute("traceloop.entity.output", _entity_output(self._reply))
            self.root.end()
