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
import re
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
    llm_env_seen: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Config":
        api_key = _env("SF_MCP_API_KEY")
        client_id = _env("AMP_AGENTID_CLIENT_ID")
        use_provider = _env("USE_LLM_PROVIDER", "false").lower() == "true"
        auth = _env("SF_MCP_AUTH").lower()
        if auth not in ("agentid", "apikey", "none"):
            auth = "apikey" if api_key else ("agentid" if client_id else "none")
        return cls(
            agent_name=_env("AGENT_NAME", "sales-copilot"),
            agent_version=_env("AGENT_VERSION", "dev"),
            company_name=_env("COMPANY_NAME", "Northwind Cloud"),
            model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            use_llm_provider=use_provider,
            # Agent Manager injects the provider URL and key under names chosen on the agent's LLM configuration.
            # The script uses LLM_PROVIDER_URL / LLM_PROVIDER_KEY, but adding the configuration in the Console
            # defaults to OPENAI_URL / OPENAI_API_KEY for the OpenAI template, so accept both.
            llm_provider_url=_env("LLM_PROVIDER_URL") or (_env("OPENAI_URL") if use_provider else ""),
            llm_provider_key=_env("LLM_PROVIDER_KEY") or (_env("OPENAI_API_KEY") if use_provider else ""),
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
            llm_env_seen=tuple(sorted(k for k in os.environ if re.search(r"(LLM|OPENAI|PROVIDER|GATEWAY)", k))),
        )

    @property
    def llm_path(self) -> str:
        return "gateway" if self.use_llm_provider else "direct"

    def problems(self) -> list[str]:
        """Human readable configuration problems. Empty means ready."""
        out: list[str] = []
        if self.use_llm_provider:
            if not self.llm_provider_url or not self.llm_provider_key:
                seen = ", ".join(self.llm_env_seen) or "none"
                names = [n for n, v in (("the provider URL", self.llm_provider_url), ("the provider key", self.llm_provider_key)) if not v]
                missing = " and ".join(names) + (" were" if len(names) > 1 else " was")
                out.append(f"USE_LLM_PROVIDER is true but {missing} not injected. Looked for LLM_PROVIDER_URL or OPENAI_URL, "
                           f"and LLM_PROVIDER_KEY or OPENAI_API_KEY. Related variables present: {seen}. "
                           "Fix: attach the LLM provider under agent > Configure > LLM Configurations, set its variable names to "
                           "LLM_PROVIDER_URL and LLM_PROVIDER_KEY, and redeploy.")
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
