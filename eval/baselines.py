"""
Three naive reference strategies every eval comparison is measured
against: always long (buy & hold), always flat (hold cash -- 0% by
definition), and always short (the mirror image of buy & hold). Together
they bracket "doing nothing smart" -- a result that only beats buy-and-hold
can still be hiding a worse-than-cash outcome in some other window, so all
three get reported side by side rather than picking the one flattering
comparison.
"""
from __future__ import annotations


def buy_hold_return(start_price: float, end_price: float) -> float:
    if not start_price:
        return 0.0
    return (end_price - start_price) / start_price


def hold_cash_return(start_price: float, end_price: float) -> float:
    return 0.0


def sell_short_return(start_price: float, end_price: float) -> float:
    if not start_price:
        return 0.0
    return (start_price - end_price) / start_price


def all_baselines(start_price: float, end_price: float) -> dict:
    return {
        "buy_hold": buy_hold_return(start_price, end_price),
        "hold_cash": hold_cash_return(start_price, end_price),
        "sell_short": sell_short_return(start_price, end_price),
    }
