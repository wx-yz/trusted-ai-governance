"""HTTP surface of the agent: the Agent Manager "Chat Agent" contract, POST /chat on port 8000."""

from __future__ import annotations

import logging
from typing import Any

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import uuid

from agent import SalesCopilot
from config import Config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sales-copilot")

CONFIG = Config.from_env()
COPILOT = SalesCopilot(CONFIG)
log.info(
    "Sales Copilot ready (agent=%s, llm=%s, salesforce auth=%s, problems=%s)",
    CONFIG.agent_name, CONFIG.llm_path, CONFIG.sf_mcp_auth, CONFIG.problems() or "none",
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    context: dict[str, Any] | None = None


app = FastAPI(title="Sales Copilot", version="1.0.0")

# Only for running the agent on a laptop. On Agent Manager the gateway already answers CORS, and adding
# the headers twice makes browsers reject the response, so this stays off by default.
if os.environ.get("ENABLE_CORS", "false").lower() == "true":
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "agent": CONFIG.agent_name,
        "agent_version": CONFIG.agent_version,
        "llm_path": CONFIG.llm_path,
        "salesforce_auth": CONFIG.sf_mcp_auth,
        "ready": not CONFIG.problems(),
        "problems": CONFIG.problems(),
    }


@app.post("/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    session_id = req.session_id or str(uuid.uuid4())
    return await COPILOT.chat(req.message, session_id, req.context)
