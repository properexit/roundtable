"""
Simulated portfolio state + audit log.

Local JSON files for now (data/portfolio.json, data/audit_log.jsonl) so the
system is runnable with zero cloud setup while we build the agent logic.
Swapping this module's storage backend to Azure Table Storage later should
not require touching the MCP server or agents at all -- that's the point of
keeping the tool layer behind a stable function contract.

Nothing here places a real order. "execute_trade" only ever mutates the
local simulated portfolio file, and only when approved=True.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.tools import market_data

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
PROPOSALS_FILE = DATA_DIR / "proposals.json"
AUDIT_LOG_FILE = DATA_DIR / "audit_log.jsonl"

STARTING_CASH = 100_000.00


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def _save_json(path: Path, data) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _append_audit(event: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": _now(), **event}
    with open(AUDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def get_state() -> dict:
    return _load_json(PORTFOLIO_FILE, {"cash": STARTING_CASH, "positions": {}})


def propose_trade(ticker: str, action: str, quantity: int, rationale: str) -> dict:
    if action not in ("buy", "sell"):
        raise ValueError(f"action must be 'buy' or 'sell', got {action!r}")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    proposals = _load_json(PROPOSALS_FILE, {})
    proposal_id = str(uuid.uuid4())[:8]
    proposal = {
        "proposal_id": proposal_id,
        "ticker": ticker.upper(),
        "action": action,
        "quantity": quantity,
        "rationale": rationale,
        "status": "pending",
        "created_at": _now(),
    }
    proposals[proposal_id] = proposal
    _save_json(PROPOSALS_FILE, proposals)
    _append_audit({"event": "trade_proposed", **proposal})
    return proposal


def execute_trade(proposal_id: str, approved: bool, approved_by: str) -> dict:
    proposals = _load_json(PROPOSALS_FILE, {})
    proposal = proposals.get(proposal_id)
    if proposal is None:
        result = {"status": "error", "reason": f"unknown proposal_id {proposal_id!r}"}
        _append_audit({"event": "trade_execution_failed", "proposal_id": proposal_id, **result})
        return result

    if not approved or not approved_by:
        proposal["status"] = "rejected"
        _save_json(PROPOSALS_FILE, proposals)
        _append_audit({
            "event": "trade_rejected",
            "proposal_id": proposal_id,
            "approved_by": approved_by,
        })
        return {"status": "rejected", "proposal_id": proposal_id}

    # Fail-closed: this is the only branch that mutates the portfolio, and it
    # requires both approved=True and a named approver.
    state = get_state()
    price = market_data.get_price(proposal["ticker"])["last_price"]
    cost = price * proposal["quantity"]

    if proposal["action"] == "buy":
        if cost > state["cash"]:
            proposal["status"] = "failed_insufficient_cash"
            _save_json(PROPOSALS_FILE, proposals)
            _append_audit({"event": "trade_failed", "reason": "insufficient_cash", "proposal_id": proposal_id})
            return {"status": "failed_insufficient_cash", "proposal_id": proposal_id}
        state["cash"] -= cost
        pos = state["positions"].setdefault(proposal["ticker"], {"quantity": 0, "avg_cost": 0.0})
        new_qty = pos["quantity"] + proposal["quantity"]
        pos["avg_cost"] = ((pos["avg_cost"] * pos["quantity"]) + cost) / new_qty
        pos["quantity"] = new_qty
    else:  # sell
        pos = state["positions"].get(proposal["ticker"])
        if pos is None or pos["quantity"] < proposal["quantity"]:
            proposal["status"] = "failed_insufficient_position"
            _save_json(PROPOSALS_FILE, proposals)
            _append_audit({"event": "trade_failed", "reason": "insufficient_position", "proposal_id": proposal_id})
            return {"status": "failed_insufficient_position", "proposal_id": proposal_id}
        pos["quantity"] -= proposal["quantity"]
        state["cash"] += cost
        if pos["quantity"] == 0:
            del state["positions"][proposal["ticker"]]

    _save_json(PORTFOLIO_FILE, state)
    proposal["status"] = "executed"
    proposal["approved_by"] = approved_by
    proposal["executed_price"] = price
    _save_json(PROPOSALS_FILE, proposals)
    _append_audit({"event": "trade_executed", "proposal_id": proposal_id, "approved_by": approved_by, "price": price})
    return {"status": "executed", "proposal_id": proposal_id, "price": price, "new_state": state}

