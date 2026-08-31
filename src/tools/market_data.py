"""
Market data tool — wraps yfinance so agents get plain dicts, not a library's
internal objects. Kept separate from the MCP server so it's independently
testable and independently swappable (e.g. for a paid data provider later)
without touching the MCP or agent layers.
"""
from __future__ import annotations

import yfinance as yf


def get_price(ticker: str) -> dict:
    """Latest price snapshot for a ticker."""
    t = yf.Ticker(ticker)
    info = t.fast_info
    return {
        "ticker": ticker.upper(),
        "last_price": round(float(info.last_price), 2),
        "previous_close": round(float(info.previous_close), 2),
        "day_high": round(float(info.day_high), 2),
        "day_low": round(float(info.day_low), 2),
        "currency": info.currency,
        "market_cap": info.market_cap,
    }


def is_indian_ticker(ticker: str) -> bool:
    """True for NSE/BSE-listed tickers, per yfinance's exchange-suffix
    convention (e.g. "PNB.NS", "SAGILITY.BO"). Used to route the News &
    Sentiment analyst to a source with real Indian financial-press coverage
    instead of NewsAPI, whose free tier's Indian coverage is thin -- see
    docs/decisions.md.
    """
    return ticker.strip().upper().endswith((".NS", ".BO"))


def resolve_company_name(ticker: str) -> str:
    """Best-effort company display name for a ticker, via yfinance.

    Used to auto-fill the News & Sentiment analyst's search query from just
    a ticker -- yfinance's ``longName`` is the same field ``get_fundamentals``
    already reads, pulled out here as its own lightweight lookup so callers
    that only need a name (not the full fundamentals payload) aren't paying
    for one. Never raises: a bad/delisted ticker or a yfinance hiccup just
    falls back to the ticker itself, since the news search still works (if
    less precisely) on the raw symbol.
    """
    try:
        info = yf.Ticker(ticker).info
        return info.get("longName") or info.get("shortName") or ticker.upper()
    except Exception:
        return ticker.upper()


def get_fundamentals(ticker: str) -> dict:
    """Core fundamental ratios used by the fundamentals analyst agent."""
    t = yf.Ticker(ticker)
    info = t.info
    return {
        "ticker": ticker.upper(),
        "name": info.get("longName"),
        "sector": info.get("sector"),
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "peg_ratio": info.get("pegRatio"),
        "price_to_book": info.get("priceToBook"),
        "debt_to_equity": info.get("debtToEquity"),
        "profit_margin": info.get("profitMargins"),
        "revenue_growth": info.get("revenueGrowth"),
        "beta": info.get("beta"),
        "52_week_high": info.get("fiftyTwoWeekHigh"),
        "52_week_low": info.get("fiftyTwoWeekLow"),
    }


def get_price_history(ticker: str, period: str = "6mo") -> list[dict]:
    """
    Daily OHLCV history, used by the risk manager for volatility and by
    eval/backtest.py to replay historical windows.
    period: any yfinance-valid period, e.g. '1mo', '6mo', '1y', '5y'.
    """
    t = yf.Ticker(ticker)
    hist = t.history(period=period)
    hist = hist.reset_index()
    return [
        {
            "date": row["Date"].strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
        }
        for _, row in hist.iterrows()
    ]


if __name__ == "__main__":
    # quick manual smoke test: python3 -m src.tools.market_data
    import json
    print(json.dumps(get_price("AAPL"), indent=2))
    print(json.dumps(get_fundamentals("AAPL"), indent=2))
    print(f"{len(get_price_history('AAPL', '1mo'))} days of history pulled")
