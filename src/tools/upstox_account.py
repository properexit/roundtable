"""
Read-only access to your real Upstox holdings/positions/funds.

This is deliberately a separate module from src/tools/portfolio.py, which
is the simulated $100k paper portfolio the agents actually propose and
execute trades against. Nothing in this file -- and nothing reachable
from it -- can place, modify, or cancel a real order: no order-placement
endpoint is ever called here, so the read-only boundary is enforced by
what code exists, not just by an API scope Upstox could change.
"""
from __future__ import annotations

import requests

from src.tools import upstox_auth_store

UPSTOX_API_BASE = "https://api.upstox.com/v2"

NOT_CONNECTED = {
    "connected": False,
    "reason": "Not connected -- log in with Upstox to see real data.",
}


def _get(path: str) -> dict:
    token = upstox_auth_store.get_valid_token()
    if not token:
        return NOT_CONNECTED

    resp = requests.get(
        f"{UPSTOX_API_BASE}{path}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code == 401:
        # Token existed but Upstox rejected it -- treat the same as "not
        # connected" rather than raising, so the frontend can just show a
        # reconnect button instead of an error page.
        return {"connected": False, "reason": "Upstox session expired -- log in with Upstox again."}
    resp.raise_for_status()
    return {"connected": True, "data": resp.json().get("data")}


def get_real_holdings() -> dict:
    return _get("/portfolio/long-term-holdings")


def get_real_positions() -> dict:
    return _get("/portfolio/short-term-positions")


def get_real_funds() -> dict:
    return _get("/user/get-funds-and-margin")

def find_real_position(ticker: str):
    """
    Looks for `ticker` among your real holdings and positions (exact,
    case-insensitive match against Upstox's own trading_symbol). Returns
    {"kind": "holding" | "position", ...the raw entry} on a match, or None.

    This is a plain string match against NSE/BSE trading symbols (e.g.
    "SAGILITY", "PNB") -- the roundtable demo mostly analyzes US tickers
    via yfinance (AAPL, NVDA, ...). A match only happens when the ticker
    typed into "Run Roundtable" is also your real trading_symbol. That's a
    real limitation of pairing a US-market demo with an Indian brokerage
    account, not a bug being papered over.
    """
    ticker = ticker.strip().upper()
    for result, kind in ((get_real_holdings(), "holding"), (get_real_positions(), "position")):
        if not result.get("connected"):
            continue
        for entry in result.get("data") or []:
            if (entry.get("trading_symbol") or "").upper() == ticker:
                return {"kind": kind, **entry}
    return None


def real_position_context_line(ticker: str) -> str | None:
    """
    One-line, LLM-ready summary of your real position in `ticker` for the
    Portfolio Manager agent to factor in -- or None, meaning "add nothing".

    None specifically means "not connected to Upstox right now" -- as
    opposed to "connected, checked, and confirmed no position", which
    returns an explicit line saying so. Collapsing those two into one
    "nothing to add" case would let the agent's prompt silently assume no
    position when the truth is just "we don't currently know."
    """
    holdings = get_real_holdings()
    positions = get_real_positions()
    if not holdings.get("connected") and not positions.get("connected"):
        return None

    match = find_real_position(ticker)
    if match is None:
        return (
            f"Note: the user has no existing real brokerage position in {ticker} "
            f"(checked live via their connected Upstox account)."
        )

    qty = match.get("quantity")
    avg = match.get("average_price")
    pnl = match.get("pnl")
    return (
        f"Note: the user has a real brokerage {match['kind']} of {qty} shares of "
        f"{ticker} at an average price of {avg} (P&L: {pnl}). This is real money, "
        f"entirely separate from this system's simulated portfolio -- use it only "
        f"as context (existing concentration, whether adding to or trimming a real "
        f"position makes sense), never as something this system will execute."
    )

