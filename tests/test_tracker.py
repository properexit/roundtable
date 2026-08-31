"""
Unit tests for the eval forward-tracking watchlist logic (eval/tracker.py).

Covers the dynamic-watchlist change: AAPL is the one fixed "dummy" entry
that guarantees a continuous track record, and the rest of the watchlist
is discovered fresh from the user's real Upstox long-term holdings on
every run -- not hand-picked, and not a config value that needs editing
when a new stock is bought. See docs/decisions.md for why this can't
simply always include real holdings (the Upstox token expires daily and
this runs unattended on a schedule).
"""
from unittest.mock import AsyncMock, MagicMock, patch

from eval import tracker


# --- _real_holdings_watchlist ---------------------------------------------

def test_real_holdings_watchlist_empty_when_not_connected():
    with patch.object(tracker.upstox_account, "get_real_holdings", return_value={"connected": False}):
        assert tracker._real_holdings_watchlist() == []


def test_real_holdings_watchlist_suffixes_ns_and_resolves_name():
    holdings = {
        "connected": True,
        "data": [
            {"trading_symbol": "pnb", "quantity": 120},
            {"trading_symbol": "SAGILITY", "quantity": 40},
        ],
    }
    with patch.object(tracker.upstox_account, "get_real_holdings", return_value=holdings), \
         patch.object(tracker.market_data, "resolve_company_name", side_effect=lambda t: f"Name for {t}"):
        result = tracker._real_holdings_watchlist()

    assert result == [
        ("PNB.NS", "Name for PNB.NS"),
        ("SAGILITY.NS", "Name for SAGILITY.NS"),
    ]


def test_real_holdings_watchlist_skips_blank_symbols():
    holdings = {"connected": True, "data": [{"trading_symbol": ""}, {"trading_symbol": "PNB"}]}
    with patch.object(tracker.upstox_account, "get_real_holdings", return_value=holdings), \
         patch.object(tracker.market_data, "resolve_company_name", return_value="Punjab National Bank"):
        result = tracker._real_holdings_watchlist()
    assert result == [("PNB.NS", "Punjab National Bank")]


def test_real_holdings_watchlist_capped_at_max():
    holdings = {
        "connected": True,
        "data": [{"trading_symbol": f"SYM{i}"} for i in range(tracker.MAX_REAL_HOLDINGS_TRACKED + 5)],
    }
    with patch.object(tracker.upstox_account, "get_real_holdings", return_value=holdings), \
         patch.object(tracker.market_data, "resolve_company_name", side_effect=lambda t: t):
        result = tracker._real_holdings_watchlist()
    assert len(result) == tracker.MAX_REAL_HOLDINGS_TRACKED


# --- record_snapshot: watchlist composition --------------------------------

def _mock_table():
    table = MagicMock()
    table.upsert_entity = MagicMock()
    return table


async def _run_record_snapshot():
    return await tracker.record_snapshot()


def test_record_snapshot_records_nothing_when_not_connected():
    # No fixed/dummy ticker anymore -- a week with no fresh Upstox login
    # records nothing at all, for any ticker. That's deliberate, not a bug.
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"final_action": "hold", "final_quantity": 0})
    table = _mock_table()

    with patch.object(tracker, "_get_analysis_graph", return_value=mock_graph), \
         patch.object(tracker, "_table_client", return_value=table), \
         patch.object(tracker.market_data, "get_price", return_value={"last_price": 200.0}), \
         patch.object(tracker.upstox_account, "get_real_holdings", return_value={"connected": False}):
        import asyncio
        recorded = asyncio.run(_run_record_snapshot())

    assert recorded == []
    mock_graph.ainvoke.assert_not_called()


def test_record_snapshot_records_only_real_holdings_when_connected():
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"final_action": "hold", "final_quantity": 0})
    table = _mock_table()
    holdings = {"connected": True, "data": [{"trading_symbol": "PNB"}]}

    with patch.object(tracker, "_get_analysis_graph", return_value=mock_graph), \
         patch.object(tracker, "_table_client", return_value=table), \
         patch.object(tracker.market_data, "get_price", return_value={"last_price": 100.0}), \
         patch.object(tracker.market_data, "resolve_company_name", return_value="Punjab National Bank"), \
         patch.object(tracker.upstox_account, "get_real_holdings", return_value=holdings):
        import asyncio
        recorded = asyncio.run(_run_record_snapshot())

    tickers_recorded = {r["PartitionKey"] for r in recorded}
    assert tickers_recorded == {"PNB.NS"}


# --- get_all_tracked_tickers ------------------------------------------------

def test_get_all_tracked_tickers_returns_distinct_sorted():
    table = MagicMock()
    table.list_entities.return_value = [
        {"PartitionKey": "AAPL"},
        {"PartitionKey": "PNB.NS"},
        {"PartitionKey": "AAPL"},
        {"PartitionKey": "SAGILITY.NS"},
    ]
    with patch.object(tracker, "_table_client", return_value=table):
        result = tracker.get_all_tracked_tickers()

    assert result == ["AAPL", "PNB.NS", "SAGILITY.NS"]
