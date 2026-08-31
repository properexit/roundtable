"""
Unit tests for src/tools/market_data.py, focused on _resolve_yf_ticker's
suffix-fallback behavior -- added after a real, reported failure: a user
typed the bare Indian trading symbol "SAGILITY" (rather than the
autocomplete-suggested "SAGILITY.NS") and got "unable to retrieve price
and fundamental data" back from the Fundamentals Analyst, because
yfinance genuinely has no ticker just called "SAGILITY".
"""
from unittest.mock import MagicMock, patch

from src.tools import market_data


def _mock_ticker_factory(working_symbols):
    """
    Returns a function usable as yf.Ticker's side_effect: constructs a
    MagicMock per symbol whose fast_info.last_price is a real number for
    every symbol in `working_symbols`, and raises for everything else
    (mirroring yfinance's actual behavior for an unrecognized symbol).
    """
    def factory(symbol):
        m = MagicMock()
        m.ticker = symbol
        if symbol in working_symbols:
            m.fast_info.last_price = 123.45
        else:
            type(m).fast_info = property(lambda self: (_ for _ in ()).throw(Exception("no data found")))
        return m
    return factory


def test_resolve_already_suffixed_ticker_is_trusted_as_is():
    calls = []

    def factory(symbol):
        calls.append(symbol)
        m = MagicMock()
        m.ticker = symbol
        return m

    with patch.object(market_data.yf, "Ticker", side_effect=factory):
        result = market_data._resolve_yf_ticker("PNB.NS")

    assert result.ticker == "PNB.NS"
    assert calls == ["PNB.NS"]  # no fallback attempts for an already-suffixed ticker


def test_resolve_bare_ticker_that_works_as_is_needs_no_fallback():
    calls = []

    def factory(symbol):
        calls.append(symbol)
        m = MagicMock()
        m.ticker = symbol
        m.fast_info.last_price = 200.0
        return m

    with patch.object(market_data.yf, "Ticker", side_effect=factory):
        result = market_data._resolve_yf_ticker("AAPL")

    assert result.ticker == "AAPL"
    assert calls == ["AAPL"]


def test_resolve_falls_back_to_ns_suffix():
    # Exactly the reported bug: bare "SAGILITY" has no data, "SAGILITY.NS" does.
    factory = _mock_ticker_factory(working_symbols={"SAGILITY.NS"})

    with patch.object(market_data.yf, "Ticker", side_effect=factory):
        result = market_data._resolve_yf_ticker("SAGILITY")

    assert result.ticker == "SAGILITY.NS"


def test_resolve_falls_back_to_bo_suffix_when_ns_also_fails():
    factory = _mock_ticker_factory(working_symbols={"XYZCO.BO"})

    with patch.object(market_data.yf, "Ticker", side_effect=factory):
        result = market_data._resolve_yf_ticker("XYZCO")

    assert result.ticker == "XYZCO.BO"


def test_resolve_returns_last_attempt_when_nothing_works():
    factory = _mock_ticker_factory(working_symbols=set())

    with patch.object(market_data.yf, "Ticker", side_effect=factory):
        result = market_data._resolve_yf_ticker("NOTREAL")

    # Honest failure: still returns *something* (the last attempt, .BO),
    # so the caller's own error handling surfaces naturally rather than
    # this function swallowing the failure silently.
    assert result.ticker == "NOTREAL.BO"


def test_get_price_uses_resolved_ticker_in_output():
    factory = _mock_ticker_factory(working_symbols={"SAGILITY.NS"})

    def price_factory(symbol):
        m = _mock_ticker_factory({"SAGILITY.NS"})(symbol)
        if symbol == "SAGILITY.NS":
            m.fast_info.previous_close = 120.0
            m.fast_info.day_high = 126.0
            m.fast_info.day_low = 119.0
            m.fast_info.currency = "INR"
            m.fast_info.market_cap = 1_000_000_000
        return m

    with patch.object(market_data.yf, "Ticker", side_effect=price_factory):
        result = market_data.get_price("SAGILITY")

    assert result["ticker"] == "SAGILITY.NS"
    assert result["last_price"] == 123.45


def test_get_fundamentals_uses_resolved_ticker_in_output():
    def fundamentals_factory(symbol):
        m = MagicMock()
        m.ticker = symbol
        if symbol == "SAGILITY.NS":
            m.fast_info.last_price = 123.45
            m.info = {"longName": "Sagility India Limited", "sector": "Industrials"}
        else:
            type(m).fast_info = property(lambda self: (_ for _ in ()).throw(Exception("no data")))
        return m

    with patch.object(market_data.yf, "Ticker", side_effect=fundamentals_factory):
        result = market_data.get_fundamentals("SAGILITY")

    assert result["ticker"] == "SAGILITY.NS"
    assert result["name"] == "Sagility India Limited"


def test_resolve_yf_ticker_skips_fallback_for_bse_suffixed_ticker():
    calls = []

    def factory(symbol):
        calls.append(symbol)
        m = MagicMock()
        m.ticker = symbol
        return m

    with patch.object(market_data.yf, "Ticker", side_effect=factory):
        market_data._resolve_yf_ticker("sagility.bo")  # lowercase, as typed

    assert calls == ["SAGILITY.BO"]
