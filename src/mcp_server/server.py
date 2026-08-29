"""
MCP tool server for Roundtable. Agents never call yfinance/news/Azure APIs
directly — they call these MCP tools. That indirection is the point: it
makes the tool surface a single, auditable, swappable contract, and it's
where the one state-changing action (execute_simulated_trade) gets gated
and logged regardless of which agent reaches it.

Run standalone for local dev:
    python3 -m src.mcp_server.server
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.tools import market_data, news, portfolio, sentiment

mcp = FastMCP(
    name="roundtable-tools",
    instructions=(
        "Tools for a multi-agent investment RESEARCH system. "
        "All data tools are read-only. execute_simulated_trade is the only "
        "state-changing tool and requires approved=True, which must come "
        "from an explicit human decision upstream, never inferred by an agent."
    ),
)


@mcp.tool()
def get_price(ticker: str) -> dict:
    """Latest price snapshot (last price, day range, market cap) for a ticker."""
    return market_data.get_price(ticker)


@mcp.tool()
def get_fundamentals(ticker: str) -> dict:
    """Core fundamental ratios (P/E, PEG, margins, growth, beta) for a ticker."""
    return market_data.get_fundamentals(ticker)


@mcp.tool()
def get_price_history(ticker: str, period: str = "6mo") -> list[dict]:
    """Daily OHLCV history for a ticker over the given period (e.g. '6mo', '1y')."""
    return market_data.get_price_history(ticker, period)


@mcp.tool()
def get_news_sentiment(company_name: str, days: int = 7) -> dict:
    """
    Recent news sentiment for a company (use the company name, e.g. 'Apple',
    not the ticker). Returns an aggregate positive/neutral/negative signal
    plus the article count it was computed over -- this is what the
    News/Sentiment Analyst agent should call, not get_news + Azure directly.
    """
    articles = news.get_news(company_name, days=days)
    result = sentiment.analyze_news_sentiment(articles)
    result["headlines"] = [a["title"] for a in articles[:5]]
    return result


@mcp.tool()
def get_portfolio_state() -> dict:
    """Current simulated portfolio: cash, positions, and their cost basis."""
    return portfolio.get_state()


@mcp.tool()
def propose_trade(ticker: str, action: str, quantity: int, rationale: str) -> dict:
    """
    Record a proposed trade for human review. Does NOT execute it.
    action: 'buy' or 'sell'. Returns a proposal_id to pass to
    execute_simulated_trade once a human has approved it.
    """
    return portfolio.propose_trade(ticker, action, quantity, rationale)


@mcp.tool()
def execute_simulated_trade(proposal_id: str, approved: bool, approved_by: str) -> dict:
    """
    Execute a previously proposed trade against the SIMULATED portfolio.
    approved must be True and approved_by must identify the human who
    approved it — this is the fail-closed gate: no proposal_id is ever
    executed without both fields set by a human-facing step, never by an
    agent inferring approval. Every call, approved or rejected, is written
    to the audit log.
    """
    return portfolio.execute_trade(proposal_id, approved, approved_by)


if __name__ == "__main__":
    mcp.run(transport="stdio")
