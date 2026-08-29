"""
Shared helper that spawns the MCP server as a subprocess (stdio transport)
and returns its tools as LangChain-compatible tool objects. Every agent
gets its tools through this -- none of them import src.tools directly.
That boundary is the point: agents only ever see the MCP contract, not the
underlying implementation, exactly like a real multi-service system.
"""
from __future__ import annotations

import os

from langchain_mcp_adapters.client import MultiServerMCPClient

# env is passed explicitly -- the MCP server runs as a spawned subprocess,
# and without this, tools that need secrets (news.py, sentiment.py -- e.g.
# NEWS_API_KEY, AZURE_LANGUAGE_KEY) fail with a KeyError even though the
# parent process has them, because subprocess env inheritance isn't
# guaranteed by langchain-mcp-adapters' stdio transport. market_data.py
# tools never surfaced this since yfinance needs no credentials.
MCP_SERVER_PARAMS = {
    "roundtable": {
        "command": "python3",
        "args": ["-m", "src.mcp_server.server"],
        "transport": "stdio",
        "env": dict(os.environ),
    }
}


async def get_mcp_tools(names: list[str] | None = None) -> list:
    """
    Returns LangChain tool objects for the running MCP server.
    names: if given, only return tools whose .name is in this list (each
    agent should only be handed the tools it actually needs -- the
    fundamentals analyst has no business holding execute_simulated_trade).
    """
    client = MultiServerMCPClient(MCP_SERVER_PARAMS)
    tools = await client.get_tools()
    if names is None:
        return tools
    return [t for t in tools if t.name in names]
