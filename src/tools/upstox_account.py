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
