"""
Unit tests for the portfolio/audit-log tool. Deliberately zero network
dependency -- market data lookups are stubbed -- so these run anywhere,
including in CI, regardless of the live-data-access question.
"""
import importlib
import json

import pytest

from src.tools import portfolio


MOCK_PRICE = 150.00


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point the module at a throwaway data dir for every test."""
    monkeypatch.setattr(portfolio, "DATA_DIR", tmp_path)
    monkeypatch.setattr(portfolio, "PORTFOLIO_FILE", tmp_path / "portfolio.json")
    monkeypatch.setattr(portfolio, "PROPOSALS_FILE", tmp_path / "proposals.json")
    monkeypatch.setattr(portfolio, "AUDIT_LOG_FILE", tmp_path / "audit_log.jsonl")
    # Trade execution needs a price; stub it so tests never hit the network
    # or depend on the market being open. See test_execute_trade_uses_live_price
    # below for the one test that intentionally exercises the real call.
    monkeypatch.setattr(
        portfolio.market_data, "get_price",
        lambda ticker: {"ticker": ticker, "last_price": MOCK_PRICE},
    )
    yield tmp_path


def test_starting_state_is_all_cash():
    state = portfolio.get_state()
    assert state["cash"] == portfolio.STARTING_CASH
    assert state["positions"] == {}


def test_propose_then_approve_executes_and_updates_state():
    proposal = portfolio.propose_trade("AAPL", "buy", 10, "test buy")
    assert proposal["status"] == "pending"

    result = portfolio.execute_trade(proposal["proposal_id"], approved=True, approved_by="uday")

    assert result["status"] == "executed"
    state = portfolio.get_state()
    assert state["positions"]["AAPL"]["quantity"] == 10
    assert state["cash"] < portfolio.STARTING_CASH


def test_unapproved_trade_never_mutates_portfolio():
    """The fail-closed guarantee: approved=False must never touch state."""
    proposal = portfolio.propose_trade("AAPL", "buy", 10, "test buy")
    result = portfolio.execute_trade(proposal["proposal_id"], approved=False, approved_by="")

    assert result["status"] == "rejected"
    assert portfolio.get_state() == {"cash": portfolio.STARTING_CASH, "positions": {}}


def test_missing_approver_is_treated_as_not_approved():
    """approved=True with no approved_by must still be rejected -- both are required."""
    proposal = portfolio.propose_trade("AAPL", "buy", 10, "test buy")
    result = portfolio.execute_trade(proposal["proposal_id"], approved=True, approved_by="")

    assert result["status"] == "rejected"
    assert portfolio.get_state()["positions"] == {}


def test_sell_more_than_held_fails_without_mutating():
    proposal = portfolio.propose_trade("AAPL", "sell", 5, "no position yet")
    result = portfolio.execute_trade(proposal["proposal_id"], approved=True, approved_by="uday")

    assert result["status"] == "failed_insufficient_position"
    assert portfolio.get_state()["positions"] == {}


def test_buy_beyond_cash_fails_without_mutating():
    proposal = portfolio.propose_trade("AAPL", "buy", 1_000_000, "too big")
    result = portfolio.execute_trade(proposal["proposal_id"], approved=True, approved_by="uday")

    assert result["status"] == "failed_insufficient_cash"
    assert portfolio.get_state()["cash"] == portfolio.STARTING_CASH


def test_every_execution_attempt_is_audit_logged():
    proposal = portfolio.propose_trade("AAPL", "buy", 10, "test buy")
    portfolio.execute_trade(proposal["proposal_id"], approved=True, approved_by="uday")

    log_lines = portfolio.AUDIT_LOG_FILE.read_text().strip().split("\n")
    events = [json.loads(line)["event"] for line in log_lines]
    assert "trade_proposed" in events
    assert "trade_executed" in events


def test_execute_trade_uses_market_data_price(monkeypatch):
    """Confirms the executed price actually comes from market_data.get_price,
    not a hardcoded value -- this is what the fixture above stubs out."""
    monkeypatch.setattr(
        portfolio.market_data, "get_price",
        lambda ticker: {"ticker": ticker, "last_price": 42.50},
    )
    proposal = portfolio.propose_trade("AAPL", "buy", 2, "price wiring check")
    result = portfolio.execute_trade(proposal["proposal_id"], approved=True, approved_by="uday")

    assert result["price"] == 42.50
    assert portfolio.get_state()["positions"]["AAPL"]["avg_cost"] == 42.50
