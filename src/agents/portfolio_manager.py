"""
Portfolio Manager -- synthesizes the other three analysts into a
recommendation. Deliberately has NO MCP tools of its own: it only reasons
over the text it's handed. Uses structured output (a Pydantic schema)
rather than asking the LLM to emit a parseable "Signal: X" line, because
the graph needs to reliably branch on this agent's decision -- regex-
parsing free text for a state-changing decision is exactly the kind of
fragility this project is supposed to avoid.

Runs in two passes: a draft (before risk review) and a final call (after).
The actual propose_trade / execute_simulated_trade MCP calls happen in
graph.py as deterministic application code, not as a tool this agent
invokes itself -- keeping "decide what to recommend" (LLM) and "actually
touch state" (code, gated by a human) as two different kinds of authority.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.llm import get_llm


class DraftRecommendation(BaseModel):
    action: Literal["buy", "sell", "hold"] = Field(
        description="'hold' is a legitimate answer when the analysts disagree or the picture is mixed."
    )
    quantity: int = Field(ge=0, description="Shares. 0 if action is 'hold'.")
    rationale: str = Field(description="2-4 sentences synthesizing the fundamentals and news views.")


class FinalRecommendation(BaseModel):
    action: Literal["buy", "sell", "hold"]
    quantity: int = Field(ge=0)
    rationale: str = Field(description="2-4 sentences, incorporating the risk assessment.")
    confidence: Literal["low", "medium", "high"] = Field(
        description="'high' should be rare -- reserved for when all analysts agree with no material risk flags."
    )


DRAFT_SYSTEM_PROMPT = """You are the Portfolio Manager on an investment research desk, \
synthesizing independent analysts into a DRAFT trade recommendation -- you have not \
seen a risk assessment yet, this is subject to revision.

Weigh the Fundamentals Analyst's valuation/financial-health view and the \
News/Sentiment Analyst's read together. This is a $100,000 simulated \
portfolio -- recommend a realistic position size (tens of shares for a \
stock in the hundreds of dollars, not thousands). If the analysts \
disagree or the picture is mixed, 'hold' is often the correct answer -- \
do not force a buy or sell to appear decisive."""

FINAL_SYSTEM_PROMPT = """You are the Portfolio Manager finalizing a trade recommendation \
after risk review. Adjust the action or quantity if the risk assessment warrants it \
(reduce size on high concentration/volatility, or override to 'hold' if risk is high \
and conviction isn't strong). State confidence honestly -- 'high' should be rare."""


async def draft_recommendation(ticker: str, fundamentals: str, news: str) -> DraftRecommendation:
    llm = get_llm().with_structured_output(DraftRecommendation)
    user_prompt = f"Ticker: {ticker}\n\nFundamentals Analyst:\n{fundamentals}\n\nNews/Sentiment Analyst:\n{news}"
    return await llm.ainvoke([
        {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])


async def finalize_recommendation(
    ticker: str, fundamentals: str, news: str, risk: str, draft: DraftRecommendation
) -> FinalRecommendation:
    llm = get_llm().with_structured_output(FinalRecommendation)
    user_prompt = (
        f"Ticker: {ticker}\n\n"
        f"Fundamentals Analyst:\n{fundamentals}\n\n"
        f"News/Sentiment Analyst:\n{news}\n\n"
        f"Draft recommendation: {draft.action} {draft.quantity} shares -- {draft.rationale}\n\n"
        f"Risk Manager assessment:\n{risk}"
    )
    return await llm.ainvoke([
        {"role": "system", "content": FINAL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
