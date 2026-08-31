"""
Unit tests for the news tool -- routing between NewsAPI and the Indian-market
path (Marketaux + Economic Times RSS fallback), and the entity-based
relevance filter that keeps Marketaux from returning multi-stock roundup
articles for a single-company query.

The _is_primary_entity fixtures below are not invented -- they're the real
`entities`/`match_score` shapes returned by Marketaux for three actual
"Punjab National Bank" search hits (see docs/decisions.md): one genuinely
on-topic article, and two broad roundups that merely mentioned PNB. See
_debug_marketaux.py (scratch, not committed) for how they were captured.
"""
from unittest.mock import MagicMock, patch

from src.tools import news


# --- _is_primary_entity ------------------------------------------------

def test_is_primary_entity_true_when_target_is_top_scored():
    # Real on-topic article: 2 entities, PNB clearly highest.
    article = {
        "entities": [
            {"symbol": "PNB", "match_score": 38.14},
            {"symbol": "SOMEOTHER", "match_score": 18.07},
        ]
    }
    assert news._is_primary_entity(article, "PNB.NS") is True


def test_is_primary_entity_false_for_multi_stock_roundup_home_loan():
    # Real noisy hit: home-loan-rates roundup, PNB present but not top.
    article = {
        "entities": [
            {"symbol": "PNB", "match_score": 36.0},
            {"symbol": "KARURVYSYA", "match_score": 47.98},
        ]
    }
    assert news._is_primary_entity(article, "PNB.NS") is False


def test_is_primary_entity_false_for_multi_stock_roundup_margin_bulletin():
    # Real noisy hit: 25-company margin bulletin, PNB present but not top.
    article = {
        "entities": [
            {"symbol": "PNB", "match_score": 43.4},
            {"symbol": "CROMPTON", "match_score": 103.78},
        ]
    }
    assert news._is_primary_entity(article, "PNB.NS") is False


def test_is_primary_entity_false_when_no_entities():
    assert news._is_primary_entity({"entities": []}, "PNB.NS") is False
    assert news._is_primary_entity({}, "PNB.NS") is False


def test_is_primary_entity_false_when_target_absent():
    article = {"entities": [{"symbol": "OTHER", "match_score": 90.0}]}
    assert news._is_primary_entity(article, "PNB.NS") is False


def test_is_primary_entity_matches_ticker_ignoring_exchange_suffix():
    # ticker passed as "PNB.NS", entity symbol from Marketaux may be bare "PNB"
    article = {"entities": [{"symbol": "PNB", "match_score": 50.0}]}
    assert news._is_primary_entity(article, "PNB.NS") is True


# --- _get_marketaux_news -------------------------------------------------

def _mock_response(articles):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": articles}
    return resp


@patch.object(news, "requests")
def test_get_marketaux_news_quotes_search_and_filters_by_entity(mock_requests):
    relevant = {
        "title": "PNB reports Q1 results",
        "description": "desc",
        "source": "economictimes.com",
        "published_at": "2026-08-20T10:00:00",
        "url": "https://example.com/1",
        "entities": [{"symbol": "PNB", "match_score": 38.14}, {"symbol": "X", "match_score": 18.07}],
    }
    noisy = {
        "title": "Home loan rates roundup",
        "description": "desc",
        "source": "economictimes.com",
        "published_at": "2026-08-20T09:00:00",
        "url": "https://example.com/2",
        "entities": [{"symbol": "PNB", "match_score": 36.0}, {"symbol": "KARURVYSYA", "match_score": 47.98}],
    }
    mock_requests.get.return_value = _mock_response([relevant, noisy])

    with patch.dict("os.environ", {"MARKETAUX_API_KEY": "fake-key"}):
        result = news._get_marketaux_news("Punjab National Bank", "PNB.NS", days=7, page_size=10)

    # Only the on-topic article survives the entity filter.
    assert len(result) == 1
    assert result[0]["title"] == "PNB reports Q1 results"

    # search param must be quoted for exact-phrase matching.
    _, kwargs = mock_requests.get.call_args
    assert kwargs["params"]["search"] == '"Punjab National Bank"'
    # raw pool requested is larger than page_size so filtering doesn't starve results.
    assert kwargs["params"]["limit"] == 25  # min(10*3, 25)


@patch.object(news, "requests")
def test_get_marketaux_news_returns_empty_without_api_key(mock_requests):
    with patch.dict("os.environ", {}, clear=True):
        result = news._get_marketaux_news("Punjab National Bank", "PNB.NS")
    assert result == []
    mock_requests.get.assert_not_called()


# --- get_indian_news fallback chain --------------------------------------

def test_get_indian_news_falls_back_to_et_rss_when_marketaux_empty():
    with patch.object(news, "_get_marketaux_news", return_value=[]) as mock_marketaux, \
         patch.object(news, "_get_et_rss_news", return_value=[{"title": "ET article"}]) as mock_rss:
        result = news.get_indian_news("Punjab National Bank", "PNB.NS", days=7, page_size=10)

    mock_marketaux.assert_called_once_with("Punjab National Bank", "PNB.NS", days=7, page_size=10)
    mock_rss.assert_called_once()
    assert result == [{"title": "ET article"}]


def test_get_indian_news_falls_back_to_et_rss_when_marketaux_raises():
    with patch.object(news, "_get_marketaux_news", side_effect=Exception("boom")), \
         patch.object(news, "_get_et_rss_news", return_value=[{"title": "ET article"}]):
        result = news.get_indian_news("Punjab National Bank", "PNB.NS")
    assert result == [{"title": "ET article"}]


def test_get_indian_news_returns_empty_when_both_sources_fail():
    with patch.object(news, "_get_marketaux_news", side_effect=Exception("boom")), \
         patch.object(news, "_get_et_rss_news", side_effect=Exception("boom too")):
        result = news.get_indian_news("Punjab National Bank", "PNB.NS")
    assert result == []


def test_get_indian_news_prefers_marketaux_when_it_has_results():
    with patch.object(news, "_get_marketaux_news", return_value=[{"title": "Marketaux article"}]) as mock_marketaux, \
         patch.object(news, "_get_et_rss_news") as mock_rss:
        result = news.get_indian_news("Punjab National Bank", "PNB.NS")

    assert result == [{"title": "Marketaux article"}]
    mock_rss.assert_not_called()


# --- get_news routing ------------------------------------------------------

def test_get_news_routes_indian_ticker_to_indian_path_with_ticker_forwarded():
    with patch.object(news, "get_indian_news", return_value=[{"title": "indian"}]) as mock_indian, \
         patch.object(news, "_get_newsapi_news") as mock_newsapi:
        result = news.get_news("Punjab National Bank", ticker="PNB.NS", days=7, page_size=10)

    mock_indian.assert_called_once_with("Punjab National Bank", "PNB.NS", days=7, page_size=10)
    mock_newsapi.assert_not_called()
    assert result == [{"title": "indian"}]


def test_get_news_routes_non_indian_ticker_to_newsapi():
    with patch.object(news, "get_indian_news") as mock_indian, \
         patch.object(news, "_get_newsapi_news", return_value=[{"title": "newsapi"}]) as mock_newsapi:
        result = news.get_news("Apple Inc", ticker="AAPL", days=7, page_size=10)

    mock_indian.assert_not_called()
    mock_newsapi.assert_called_once_with("Apple Inc", days=7, page_size=10)
    assert result == [{"title": "newsapi"}]


def test_get_news_routes_to_newsapi_when_no_ticker_given():
    with patch.object(news, "get_indian_news") as mock_indian, \
         patch.object(news, "_get_newsapi_news", return_value=[{"title": "newsapi"}]) as mock_newsapi:
        result = news.get_news("Apple Inc")

    mock_indian.assert_not_called()
    mock_newsapi.assert_called_once()
    assert result == [{"title": "newsapi"}]
