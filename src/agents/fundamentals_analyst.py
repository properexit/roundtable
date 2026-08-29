"""
Fundamentals Analyst agent -- the first of the three independent analysts
in the roundtable. Reasons about a ticker's price and fundamental ratios
only; it deliberately has no access to news, portfolio state, or trade
tools, so its output is a clean, single-purpose signal for the Portfolio
Manager to weigh alongside the other analysts.
"""
from __future__ import annotations

from langgraph.prebuilt import create_react_agent

from src.agents.mcp_client import get_mcp_tools
from src.llm import get_llm

SYSTEM_PROMPT = """You are the Fundamentals Analyst on an investment research desk.

You evaluate a stock using price data and fundamental ratios only -- you do
not have access to news or sentiment, that is a different analyst's job.

Given a ticker, call the available tools to pull its price snapshot,
fundamental ratios, and recent price history, then produce a concise
analysis covering: valuation (is it cheap/expensive vs. what you'd expect
for its sector and growth), financial health (margins, debt), and recent
price trend. End with a single line: "Signal: bullish / neutral / bearish"
and one sentence of justification.

This is research and analysis only. You are not giving investment advice
and must not recommend a specific trade -- that decision belongs to the
Portfolio Manager after weighing all analysts and a human's approval.
Be specific and cite the actual numbers you were given, don't hand-wave."""


async def analyze(ticker: str) -> str:
    tools = await get_mcp_tools(names=["get_price", "get_fundamentals", "get_price_history"])
    agent = create_react_agent(model=get_llm(), tools=tools, prompt=SYSTEM_PROMPT)

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": f"Analyze {ticker}."}]}
    )
    return result["messages"][-1].content


if __name__ == "__main__":
    import asyncio
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(asyncio.run(analyze(ticker)))
