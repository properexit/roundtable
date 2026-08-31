"""
Forward-tracking evaluation of the real agent system.

Runs the analysis-only graph (src/agents/graph.build_analysis_graph --
no trade execution, nothing to approve) against a watchlist on a schedule,
logs each decision plus the price at that moment to Azure Table Storage,
and reconstructs a simple simulated equity curve from those decisions for
comparison against the three naive baselines in eval/baselines.py.

The watchlist itself is AAPL (a fixed dummy entry, always tracked, so the
track record has continuous history independent of anything account-
specific) plus whatever is currently in the user's real Upstox long-term
holdings, refetched fresh on every run -- see _real_holdings_watchlist.

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
from src.tools import market_data, upstox_account

load_dotenv()

TABLE_NAME = "EvalSnapshots"

# AAPL is the one fixed, always-on entry -- deliberately a well-known
# dummy example, not a portfolio holding. It exists so the track record
# has a continuous history from day one, independent of whether the user
# is logged into Upstox on any given week (see _real_holdings_watchlist
# below for why that can't be relied on for every run).
BASE_WATCHLIST = [
    ("AAPL", "Apple Inc"),
]

# Max additional tickers pulled from real holdings per run. Each one costs
# one NewsAPI/Marketaux call and one Azure OpenAI round trip per agent, and
# an unbounded real portfolio could quietly blow through the shared
# free-tier quota this already runs close to -- see the WATCHLIST history
# in docs/decisions.md. Ten is comfortably above any real holdings list
# this project has seen so far.
MAX_REAL_HOLDINGS_TRACKED = 10


def _real_holdings_watchlist() -> list[tuple[str, str]]:
    """
    Best-effort (ticker, company_name) pairs from the user's real Upstox
    long-term holdings, so the track record grows with whatever is
    actually owned instead of a hand-picked list -- buy something new and
    it starts showing up here the next time a snapshot runs. Long-term
    holdings only, not short-term/derivatives positions (those can be F&O
    contracts that don't price the same way through yfinance). NSE-listed,
    so ".NS" is appended to match yfinance's convention (see
    market_data.is_indian_ticker) -- the same suffixing the frontend
    already does for its ticker-suggestions datalist.

    Returns [] whenever the Upstox token isn't valid at snapshot time. It
    expires daily (see upstox_auth_store.py) and this runs unattended on a
    weekly schedule, so most runs will not land on a day with a fresh
    login -- that's expected, not an error. AAPL alone keeps the track
    record continuous on those weeks; holdings pick back up automatically
    whenever a run does land on a day the token is still valid.
    """
    result = upstox_account.get_real_holdings()
    if not result.get("connected"):
        return []

    pairs = []
    for entry in (result.get("data") or [])[:MAX_REAL_HOLDINGS_TRACKED]:
        symbol = (entry.get("trading_symbol") or "").strip()
        if not symbol:
            continue
        ticker = f"{symbol.upper()}.NS"
        company_name = market_data.resolve_company_name(ticker)
        pairs.append((ticker, company_name))
    return pairs

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

    watchlist = BASE_WATCHLIST + _real_holdings_watchlist()

    for ticker, company_name in watchlist:
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


def get_all_tracked_tickers() -> list[str]:
    """
    Every ticker that has at least one recorded snapshot, past or present
    -- not just this week's watchlist. A ticker recorded weeks ago (AAPL
    from day one, or a holding since sold) still has a track record worth
    showing; it just stops growing new entries once it drops off the
    dynamic watchlist. Table Storage has no cheap "distinct partition
    keys" query, so this scans every row -- fine at this scale (a handful
    of tickers, one row per ticker per week).
    """
    table = _table_client()
    entities = table.list_entities(select=["PartitionKey"])
    return sorted({e["PartitionKey"] for e in entities})


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
