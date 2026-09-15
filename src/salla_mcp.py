"""LiveKit MCP wiring for Salla Platform Docs (Apidog) + live Merchant API."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Default matches the Cursor / Apidog MCP config supplied for Salla docs.
DEFAULT_APIDOG_SITE_ID = "451700"
DEFAULT_APIDOG_PACKAGE = "apidog-mcp-server@latest"
MAX_MCP_RESULT_CHARS = int(os.getenv("SALLA_MCP_RESULT_MAX_CHARS", "4000"))


def salla_mcp_enabled() -> bool:
    return os.getenv("SALLA_MCP_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def salla_merchant_mcp_enabled() -> bool:
    return os.getenv("SALLA_MERCHANT_MCP_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def truncating_mcp_resolver(ctx: Any) -> str:
    """Keep OAS / API payloads short enough for a voice LLM turn."""
    result = getattr(ctx, "result", None)
    content = getattr(result, "content", None) or []
    if len(content) == 1 and hasattr(content[0], "model_dump_json"):
        text = str(content[0].model_dump_json())
    elif content:
        payload = []
        for item in content:
            if hasattr(item, "model_dump"):
                payload.append(item.model_dump())
            else:
                payload.append(str(item))
        text = json.dumps(payload, ensure_ascii=False, default=str)
    else:
        return "Tool returned no content."

    if len(text) > MAX_MCP_RESULT_CHARS:
        return text[:MAX_MCP_RESULT_CHARS] + "\n... [truncated]"
    return text


def _merchant_env() -> dict[str, str]:
    """Forward current process env (incl. dotenv) into the MCP child."""
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    # Ensure critical Salla keys are present even if somehow unset upstream.
    for key in (
        "SALLA_STORE_ID",
        "SALLA_CLIENT_ID",
        "SALLA_CLIENT_SECRET",
        "SALLA_ACCESS_TOKEN",
        "SALLA_REFRESH_TOKEN",
        "SALLA_EXPIRES_AT",
    ):
        val = os.getenv(key)
        if val is not None:
            env[key] = val
    return env


def _merchant_server_command() -> tuple[str, list[str]]:
    """Run the local FastMCP merchant server with the same interpreter."""
    server_path = Path(__file__).resolve().parent / "salla_merchant_mcp_server.py"
    return sys.executable, [str(server_path)]


def build_salla_mcp_tools() -> list[Any]:
    """
    Build LiveKit MCP toolsets for the Salla seller assistant.

    1) Apidog `Salla Platform Docs` — OpenAPI discovery (`--site-id=451700`)
    2) Local merchant MCP — live products / orders / customers via Salla API

    You do NOT need to start these servers yourself: LiveKit spawns them over
    stdio when the agent session starts.
    """
    if not salla_mcp_enabled():
        logger.info("Salla MCP disabled via SALLA_MCP_ENABLED")
        return []

    try:
        from livekit.agents import mcp
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "livekit.agents.mcp unavailable ({}). Install with: "
            "uv add 'livekit-agents[mcp]'",
            exc,
        )
        return []

    tools: list[Any] = []
    timeout = float(os.getenv("SALLA_MCP_CONNECT_TIMEOUT", "120"))
    site_id = os.getenv("SALLA_APIDOG_SITE_ID", DEFAULT_APIDOG_SITE_ID)
    package = os.getenv("SALLA_MCP_PACKAGE", DEFAULT_APIDOG_PACKAGE)

    docs_server_kwargs: dict[str, Any] = {
        "command": os.getenv("SALLA_MCP_COMMAND", "npx"),
        "args": ["-y", package, f"--site-id={site_id}"],
        "client_session_timeout_seconds": timeout,
        "tool_result_resolver": truncating_mcp_resolver,
    }
    docs_server = mcp.MCPServerStdio(**docs_server_kwargs)

    if hasattr(mcp, "MCPToolset"):
        tools.append(
            mcp.MCPToolset(
                id="salla-platform-docs",
                mcp_server=docs_server,
            )
        )
    else:
        # Older livekit-agents: attach via Agent(mcp_servers=[...])
        tools.append(("mcp_servers", docs_server))

    if salla_merchant_mcp_enabled():
        command, args = _merchant_server_command()
        merchant_server = mcp.MCPServerStdio(
            command=command,
            args=args,
            env=_merchant_env(),
            client_session_timeout_seconds=timeout,
            tool_result_resolver=truncating_mcp_resolver,
        )
        if hasattr(mcp, "MCPToolset"):
            tools.append(
                mcp.MCPToolset(
                    id="salla-merchant-api",
                    mcp_server=merchant_server,
                )
            )
        else:
            tools.append(("mcp_servers", merchant_server))

    logger.info(
        "Prepared Salla MCP endpoints docs=true merchant={} site_id={} "
        "store_id_set={} access_token_set={}",
        salla_merchant_mcp_enabled(),
        site_id,
        bool(os.getenv("SALLA_STORE_ID")),
        bool(os.getenv("SALLA_ACCESS_TOKEN")),
    )
    return tools


def split_mcp_agent_kwargs(raw_tools: list[Any]) -> dict[str, Any]:
    """Normalize build_salla_mcp_tools() output into Agent constructor kwargs."""
    toolsets: list[Any] = []
    servers: list[Any] = []
    for item in raw_tools:
        if isinstance(item, tuple) and item and item[0] == "mcp_servers":
            servers.append(item[1])
        else:
            toolsets.append(item)

    kwargs: dict[str, Any] = {}
    if toolsets:
        kwargs["tools"] = toolsets
    if servers:
        kwargs["mcp_servers"] = servers
    return kwargs
