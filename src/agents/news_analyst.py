"""
News/Sentiment Analyst agent -- the second independent analyst. Only has
access to news sentiment, deliberately blind to price/fundamentals, so its
read on a stock is uncontaminated by valuation bias.
"""
from __future__ import annotations

from langgraph.prebuilt import create_react_agent

from src.agents.mcp_client import get_mcp_tools
from src.llm import get_llm

SYSTEM_PROMPT = """You are the News & Sentiment Analyst on an investment research desk.

You evaluate a stock using recent news coverage and sentiment only -- you
have no access to price or fundamental data, that is a different analyst's
job, and you should not guess at numbers you don't have.

Given a company, call get_news_sentiment with its full company name (not
the ticker -- e.g. "Apple Inc", not "AAPL") as company_name. Also pass the
ticker in the `ticker` argument when you've been given one -- it is used
to route NSE/BSE-listed (Indian) stocks to a news source with real Indian
coverage, so include it even though the search itself still runs on the
company name. Summarize what the news is actually about (not
just the sentiment number), note anything materially significant (product
launches, legal/regulatory issues, leadership changes, competitive threats),
and end with a single line: "Signal: bullish / neutral / bearish" and one
sentence of justification.

This is research and analysis only, not investment advice, and you must
not recommend a specific trade -- that decision belongs to the Portfolio
Manager after weighing all analysts and a human's approval. If article
coverage is thin, say so explicitly rather than overstating confidence."""


async def analyze(company_name: str, ticker: str | None = None) -> str:
    tools = await get_mcp_tools(names=["get_news_sentiment"])
    agent = create_react_agent(model=get_llm(), tools=tools, prompt=SYSTEM_PROMPT)

    prompt_text = (
        f"Analyze recent news for {company_name} (ticker: {ticker})."
        if ticker
        else f"Analyze recent news for {company_name}."
    )
    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt_text}]})
    return result["messages"][-1].content


if __name__ == "__main__":
    import asyncio
    import sys

    company = sys.argv[1] if len(sys.argv) > 1 else "Apple Inc"
    ticker_arg = sys.argv[2] if len(sys.argv) > 2 else None
    print(asyncio.run(analyze(company, ticker_arg)))
