"""
Forward-tracking evaluation of the real agent system.

Runs the analysis-only graph (src/agents/graph.build_analysis_graph --
no trade execution, nothing to approve) against a small fixed watchlist on
a schedule, logs each decision plus the price at that moment to Azure
Table Storage, and reconstructs a simple simulated equity curve from those
decisions for comparison against the three naive baselines in
eval/baselines.py.

This exists instead of a classic historical backtest because the News/
Sentiment agent's data source (NewsAPI free tier) only covers the last
~30 days -- there is no honest way to backtest the full four-agent system
further back than that. See docs/decisions.md and eval/backtest.py (a
separate, clearly-labeled historical comparison that uses a rule-based
strategy instead of the agents, for exactly this reason).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from azure.data.tables import TableServiceClient
from dotenv import load_dotenv

from src.agents.graph import build_analysis_graph
from src.tools import market_data

load_dotenv()

TABLE_NAME = "EvalSnapshots"

# Kept small and fixed on purpose: each entry costs one NewsAPI call (free
# tier: 100/day total, shared with the public demo) and one Azure OpenAI
# round trip per agent. Three tickers, run weekly, is comfortably
# sustainable; a larger or on-demand-configurable watchlist would need a
# real quota budget worked out first.
WATCHLIST = [
    ("AAPL", "Apple Inc"),
    ("MSFT", "Microsoft Corporation"),
    ("NVDA", "NVIDIA Corporation"),
]

_analysis_graph = None


def _get_analysis_graph():
    global _analysis_graph
    if _analysis_graph is None:
        _analysis_graph = build_analysis_graph()
    return _analysis_graph


def _table_client():
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    service = TableServiceClient.from_connection_string(conn_str)
    try:
        service.create_table(TABLE_NAME)
    except Exception:
        pass  # already exists
    return service.get_table_client(TABLE_NAME)


async def record_snapshot() -> list[dict]:
    """
    Runs the full analysis (fundamentals + news + risk + final
    recommendation) for every ticker in WATCHLIST and records the decision
    plus the current price. Returns what was recorded.
    """
    graph = _get_analysis_graph()
    table = _table_client()
    now = datetime.now(timezone.utc)
    recorded = []

    for ticker, company_name in WATCHLIST:
        result = await graph.ainvoke({"ticker": ticker, "company_name": company_name})
        price = market_data.get_price(ticker)

        entity = {
            "PartitionKey": ticker,
            # RowKey sorts lexicographically -- ISO 8601 sorts the same way
            # chronologically, so a plain query already comes back ordered.
            "RowKey": now.isoformat(),
            "date": now.date().isoformat(),
            "price": price["last_price"],
            "action": result.get("final_action", "hold"),
            "quantity": result.get("final_quantity", 0),
            "confidence": result.get("final_confidence", ""),
            "rationale": result.get("final_rationale", ""),
        }
        table.upsert_entity(entity)
        recorded.append(entity)

    return recorded


def get_snapshots(ticker: str) -> list[dict]:
    table = _table_client()
    entities = table.query_entities(f"PartitionKey eq '{ticker.upper()}'")
    return sorted(entities, key=lambda e: e["RowKey"])


def compute_agent_performance(snapshots: list[dict], starting_cash: float = 10_000.0) -> dict:
    """
    Replays a ticker's recorded decisions in order, as if a fixed notional
    amount had literally followed every signal: buy spends cash for
    shares, sell converts shares back to cash, hold is a no-op. This is a
    simple sequential replay, not a claim about a real account -- no fees,
    no slippage, and a buy is skipped outright if there isn't enough cash
    rather than partially filled.
    """
    cash = starting_cash
    shares = 0.0

    for snap in snapshots:
        price = snap.get("price") or 0
        action = snap.get("action")
        qty = snap.get("quantity") or 0
        if action == "buy" and qty > 0 and price > 0:
            cost = qty * price
            if cost <= cash:
                cash -= cost
                shares += qty
        elif action == "sell" and qty > 0:
            sell_qty = min(qty, shares)
            cash += sell_qty * price
            shares -= sell_qty
        # hold: no-op

    last_price = snapshots[-1]["price"] if snapshots else 0
    ending_value = cash + shares * last_price
    total_return = (ending_value - starting_cash) / starting_cash if starting_cash else 0.0

    return {
        "starting_cash": starting_cash,
        "ending_value": round(ending_value, 2),
        "cash": round(cash, 2),
        "shares": shares,
        "total_return": total_return,
        "snapshot_count": len(snapshots),
    }
