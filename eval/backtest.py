"""
Historical backtest of a fully rule-based strategy (moving-average
crossover) against real historical prices -- NOT the LLM agent system.

Why not the agents: the News/Sentiment agent's data source (NewsAPI free
tier) only covers the last ~30 days, and yfinance's fundamentals
(src/tools/market_data.get_fundamentals) are always *current*, not
point-in-time historical -- there's no honest way to feed either into a
decision dated years ago without quietly leaking present-day information
into a "historical" result. A moving-average crossover needs nothing but
historical closing prices, which yfinance genuinely has for years back, so
it's the one strategy that can be backtested honestly over a long window.

This is a second, clearly-separate reference point from eval/tracker.py's
forward-tracking of the real agent system -- see docs/decisions.md for the
full reasoning, and treat this module's numbers as "how a simple
systematic rule would have done," not as a proxy for the agents.
"""
from __future__ import annotations

from eval.baselines import all_baselines
from src.tools import market_data

STARTING_CASH = 10_000.0


def sma_crossover_backtest(
    ticker: str,
    period: str = "2y",
    short_window: int = 20,
    long_window: int = 50,
) -> dict:
    """
    Classic golden-cross / death-cross rule: go all-in when the short
    moving average crosses above the long one, go to cash when it crosses
    back below. No shorting, no fees/slippage modeled -- deliberately the
    simplest version of this strategy, so it reads as a baseline rule, not
    a tuned trading system.
    """
    history = market_data.get_price_history(ticker, period=period)
    if len(history) < long_window + 2:
        raise ValueError(
            f"Not enough price history ({len(history)} days) for a {long_window}-day moving average."
        )

    closes = [day["close"] for day in history]
    cash = STARTING_CASH
    shares = 0.0
    trades: list[dict] = []

    def sma(end_idx: int, window: int) -> float:
        return sum(closes[end_idx - window:end_idx]) / window

    for i in range(long_window, len(closes)):
        short_ma, long_ma = sma(i, short_window), sma(i, long_window)
        prev_short_ma, prev_long_ma = sma(i - 1, short_window), sma(i - 1, long_window)
        price = closes[i]

        golden_cross = prev_short_ma <= prev_long_ma and short_ma > long_ma
        death_cross = prev_short_ma >= prev_long_ma and short_ma < long_ma

        if golden_cross and cash > 0:
            shares = cash / price
            cash = 0.0
            trades.append({"date": history[i]["date"], "action": "buy", "price": price})
        elif death_cross and shares > 0:
            cash = shares * price
            shares = 0.0
            trades.append({"date": history[i]["date"], "action": "sell", "price": price})

    ending_value = cash + shares * closes[-1]
    strategy_return = (ending_value - STARTING_CASH) / STARTING_CASH

    return {
        "ticker": ticker.upper(),
        "period": period,
        "short_window": short_window,
        "long_window": long_window,
        "start_date": history[long_window]["date"],
        "end_date": history[-1]["date"],
        "trade_count": len(trades),
        "trades": trades,
        "strategy_return": strategy_return,
        "ending_value": round(ending_value, 2),
        "baselines": all_baselines(closes[long_window], closes[-1]),
    }
