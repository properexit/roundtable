"""
Risk Manager agent -- the third analyst, and the only one that looks at the
portfolio as a whole rather than a single stock in isolation. Evaluates a
proposed trade against portfolio-level concerns: concentration, volatility,
and cash sufficiency. It does not decide whether the trade happens -- it
flags risk, the Portfolio Manager (and ultimately the human) decides.
"""
from __future__ import annotations

from langgraph.prebuilt import create_react_agent

from src.agents.mcp_client import get_mcp_tools
from src.llm import get_llm

SYSTEM_PROMPT = """You are the Risk Manager on an investment research desk.

You evaluate a PROPOSED trade against portfolio-level risk, not the stock's
merits in isolation -- valuation and news are other analysts' jobs.

Given a ticker and a proposed action/quantity, call get_portfolio_state to
see current cash and positions, and get_price_history for the ticker to
gauge recent volatility (compute a rough sense of daily price swings from
the closes you're given). Assess:
- Concentration: what fraction of the portfolio's total value would this
  position represent after the trade, and is that too large a bet on one
  name?
- Cash sufficiency: is there enough cash for a buy, or enough held quantity
  for a sell?
- Volatility: is this a stock whose price swings are large enough that
  position size should be more conservative?

End with a single line: "Risk: low / medium / high" and one sentence of
justification. This is a risk read, not a go/no-go decision -- the
Portfolio Manager and a human make that call, not you."""


async def analyze(ticker: str, action: str, quantity: int) -> str:
    tools = await get_mcp_tools(names=["get_portfolio_state", "get_price_history"])
    agent = create_react_agent(model=get_llm(), tools=tools, prompt=SYSTEM_PROMPT)

    prompt = f"Assess the risk of a proposed trade: {action} {quantity} shares of {ticker}."
    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    return result["messages"][-1].content


if __name__ == "__main__":
    import asyncio
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    action = sys.argv[2] if len(sys.argv) > 2 else "buy"
    quantity = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    print(asyncio.run(analyze(ticker, action, quantity)))
