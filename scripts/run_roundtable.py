"""
CLI driver for the Roundtable graph. Runs the full analyst pipeline, prints
each stage as it completes, and -- if a trade is recommended -- pauses for
your explicit approval before anything touches the simulated portfolio.

Usage:
    python3 -m scripts.run_roundtable AAPL "Apple Inc"
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from langgraph.types import Command

from src.agents.graph import build_graph


async def main(ticker: str, company_name: str) -> None:
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    print(f"\n=== Roundtable: {ticker} ({company_name}) ===\n")
    print("Running Fundamentals + News analysts in parallel...\n")

    result = await graph.ainvoke(
        {"ticker": ticker, "company_name": company_name}, config=config
    )

    if "__interrupt__" not in result:
        # No trade proposed -- draft or final landed on 'hold'.
        print(f"Final recommendation: {result.get('final_action', 'hold').upper()}")
        print(f"Rationale: {result.get('final_rationale', '(no trade proposed)')}\n")
        return

    payload = result["__interrupt__"][0].value
    print("--- Recommendation ---")
    print(f"Action:     {payload['action'].upper()} {payload['quantity']} shares of {payload['ticker']}")
    print(f"Confidence: {payload['confidence']}")
    print(f"Rationale:  {payload['rationale']}")
    print(f"Proposal ID: {payload['proposal_id']}\n")

    answer = input("Approve this trade? [y/N]: ").strip().lower()
    approved = answer == "y"
    approved_by = ""
    if approved:
        approved_by = input("Your name (for the audit log): ").strip() or "unknown"

    final_result = await graph.ainvoke(
        Command(resume={"approved": approved, "approved_by": approved_by}),
        config=config,
    )

    print("\n--- Execution result ---")
    print(final_result["execution_result"])


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    company_name = sys.argv[2] if len(sys.argv) > 2 else "Apple Inc"
    asyncio.run(main(ticker, company_name))
