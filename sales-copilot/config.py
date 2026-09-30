"""Runtime configuration for Sales Copilot, read from environment variables.

The same code runs as two different Agent Manager agents. What differs is configuration:

* ungoverned: OPENAI_API_KEY and SF_MCP_API_KEY are set by hand. The agent holds both raw
  credentials and talks to OpenAI and to the Salesforce MCP server directly.
* governed: Agent Manager injects LLM_PROVIDER_URL / LLM_PROVIDER_KEY (an LLM provider with
  guardrails), SF_MCP_URL (an identity-secured MCP proxy) and the AMP_AGENTID_* credential.
  The agent holds no upstream credential at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    agent_name: str
    agent_version: str
    company_name: str
    model: str
    use_llm_provider: bool
    llm_provider_url: str
    llm_provider_key: str
    openai_api_key: str
    sf_mcp_url: str
    sf_mcp_auth: str  # "agentid" | "apikey" | "none"
    sf_mcp_api_key: str
    agentid_client_id: str
    agentid_client_secret: str
    agentid_token_endpoint: str
    agentid_scopes: str
    max_tool_steps: int
    history_turns: int

    @classmethod
    def from_env(cls) -> "Config":
        api_key = _env("SF_MCP_API_KEY")
        client_id = _env("AMP_AGENTID_CLIENT_ID")
        auth = _env("SF_MCP_AUTH").lower()
        if auth not in ("agentid", "apikey", "none"):
            auth = "apikey" if api_key else ("agentid" if client_id else "none")
        return cls(
            agent_name=_env("AGENT_NAME", "sales-copilot"),
            agent_version=_env("AGENT_VERSION", "dev"),
            company_name=_env("COMPANY_NAME", "Northwind Cloud"),
            model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            use_llm_provider=_env("USE_LLM_PROVIDER", "false").lower() == "true",
            llm_provider_url=_env("LLM_PROVIDER_URL"),
            llm_provider_key=_env("LLM_PROVIDER_KEY"),
            openai_api_key=_env("OPENAI_API_KEY"),
            sf_mcp_url=_env("SF_MCP_URL"),
            sf_mcp_auth=auth,
            sf_mcp_api_key=api_key,
            agentid_client_id=client_id,
            agentid_client_secret=_env("AMP_AGENTID_CLIENT_SECRET"),
            agentid_token_endpoint=_env("AMP_AGENTID_TOKEN_ENDPOINT"),
            agentid_scopes=_env("AMP_AGENTID_SCOPES"),
            max_tool_steps=int(_env("MAX_TOOL_STEPS", "8")),
            history_turns=int(_env("HISTORY_TURNS", "6")),
        )

    @property
    def llm_path(self) -> str:
        return "gateway" if self.use_llm_provider else "direct"

    def problems(self) -> list[str]:
        """Human readable configuration problems. Empty means ready."""
        out: list[str] = []
        if self.use_llm_provider:
            if not self.llm_provider_url or not self.llm_provider_key:
                out.append("USE_LLM_PROVIDER is true but LLM_PROVIDER_URL / LLM_PROVIDER_KEY are not set "
                           "(attach an LLM configuration to this agent).")
        elif not self.openai_api_key:
            out.append("OPENAI_API_KEY is not set.")
        if not self.sf_mcp_url:
            out.append("SF_MCP_URL is not set (attach the Salesforce MCP server as a tool configuration, "
                       "or set it by hand).")
        if self.sf_mcp_auth == "apikey" and not self.sf_mcp_api_key:
            out.append("SF_MCP_AUTH=apikey but SF_MCP_API_KEY is not set.")
        if self.sf_mcp_auth == "agentid" and not (
            self.agentid_client_id and self.agentid_client_secret and self.agentid_token_endpoint
        ):
            out.append("AgentID credentials (AMP_AGENTID_*) are not provisioned yet. "
                       "This can take a minute after the agent is created.")
        return out
