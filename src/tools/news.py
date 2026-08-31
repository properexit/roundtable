"""
News tool -- pulls recent headlines/articles for a company. Kept
deliberately dumb (just fetch + shape the response into a common
{title, description, source, published_at, url} list); sentiment scoring
lives in sentiment.py so this module has exactly one job.

Two sources, routed by ticker, not one -- see docs/decisions.md:
NewsAPI's free tier has thin, inconsistent coverage of Indian financial
press (no confirmed presence of Economic Times, Moneycontrol, Business
Standard, etc.), so a bare "search by company name" call against it for an
NSE/BSE-listed stock tends to come back noisy or empty. Indian tickers
(detected via market_data.is_indian_ticker -- the ".NS"/".BO" suffix)
route to Marketaux instead (confirmed real Indian-publisher coverage),
falling back to Economic Times' free Markets RSS feed if Marketaux has no
key configured, errors, or returns nothing.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from src.tools.market_data import is_indian_ticker

load_dotenv()

NEWS_API_URL = "https://newsapi.org/v2/everything"
MARKETAUX_URL = "https://api.marketaux.com/v1/news/all"
ET_MARKETS_RSS_URL = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"


def get_news(query: str, ticker: str | None = None, days: int = 7, page_size: int = 10) -> list[dict]:
    """
    Recent news for a company. `query` should be the company name (e.g.
    "Apple"), not the ticker -- both underlying APIs match on article
    text, and tickers alone produce noisy/irrelevant results. Pass
    `ticker` so an NSE/BSE-listed stock is routed to the Indian-coverage
    path automatically; omit it (or pass a non-Indian ticker) for the
    original NewsAPI behavior.
    """
    if ticker and is_indian_ticker(ticker):
        return get_indian_news(query, ticker, days=days, page_size=page_size)
    return _get_newsapi_news(query, days=days, page_size=page_size)


def get_indian_news(query: str, ticker: str, days: int = 7, page_size: int = 10) -> list[dict]:
    """News for an Indian-listed company: Marketaux first, Economic Times'
    Markets RSS feed as a free/keyless fallback. Never raises -- a bad key,
    network hiccup, or empty result just falls through to the next source,
    and an empty list (rather than irrelevant substitute articles) is the
    honest result if nothing genuinely matches, so the agent can say
    coverage is thin instead of reasoning over noise.
    """
    try:
        articles = _get_marketaux_news(query, ticker, days=days, page_size=page_size)
        if articles:
            return articles
    except Exception:
        pass
    try:
        return _get_et_rss_news(query, page_size=page_size)
    except Exception:
        return []


def _get_newsapi_news(query: str, days: int = 7, page_size: int = 10) -> list[dict]:
    api_key = os.environ["NEWS_API_KEY"]
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    resp = requests.get(
        NEWS_API_URL,
        params={
            "q": query,
            "from": from_date,
            "sortBy": "relevancy",
            "language": "en",
            "pageSize": page_size,
            "apiKey": api_key,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    return [
        {
            "title": a["title"],
            "description": a.get("description"),
            "source": a["source"]["name"],
            "published_at": a["publishedAt"],
            "url": a["url"],
        }
        for a in data.get("articles", [])
    ]


def _is_primary_entity(article: dict, ticker: str) -> bool:
    """True if `ticker` is the article's highest-match_score entity --
    i.e. the article is actually *about* this company, not a multi-stock
    roundup that happens to mention it. Confirmed empirically: a genuinely
    on-topic article had 2 entities with the target clearly highest-scored;
    two "Punjab National Bank" search hits that were actually a generic
    home-loan-rates roundup and a 25-company margin bulletin both had the
    target as a middling, not top, score among 20+ other companies.
    """
    entities = article.get("entities") or []
    if not entities:
        return False

    base_symbol = ticker.strip().upper().split(".")[0]
    target_scores = []
    other_scores = []
    for e in entities:
        symbol = (e.get("symbol") or "").upper()
        score = e.get("match_score") or 0
        if symbol.split(".")[0] == base_symbol:
            target_scores.append(score)
        else:
            other_scores.append(score)

    if not target_scores:
        return False
    return max(target_scores) >= max(other_scores, default=0)


def _get_marketaux_news(query: str, ticker: str, days: int = 7, page_size: int = 10) -> list[dict]:
    """Free tier: 100 requests/day. Returns [] rather than raising when no
    key is configured, so get_indian_news falls through to the RSS
    fallback instead of erroring the whole analysis for a missing key.

    Fetches a larger raw pool than `page_size` and filters it down via
    `_is_primary_entity` -- Marketaux's own relevance ranking still lets
    through multi-stock roundup articles where the target company is only
    a minor mention, so the entity match_score is what actually decides
    relevance here, not the API's result order.
    """
    api_key = os.environ.get("MARKETAUX_API_KEY")
    if not api_key:
        return []

    raw_limit = min(page_size * 3, 25)
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    resp = requests.get(
        MARKETAUX_URL,
        params={
            # Quoted for an exact-phrase match (Marketaux's documented "" operator).
            # Unquoted, multi-word company names get OR-matched word by word, so
            # "Punjab National Bank" pulled back any article containing the very
            # common word "bank" -- confirmed empirically, not just a guess.
            "search": f'"{query}"',
            "countries": "in",
            "published_after": from_date,
            "language": "en",
            "limit": raw_limit,
            "api_token": api_key,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    articles = [a for a in data.get("data", []) if _is_primary_entity(a, ticker)]
    return [
        {
            "title": a["title"],
            "description": a.get("description") or a.get("snippet"),
            "source": a.get("source"),
            "published_at": a.get("published_at"),
            "url": a.get("url"),
        }
        for a in articles[:page_size]
    ]


def _get_et_rss_news(query: str, page_size: int = 10) -> list[dict]:
    """Economic Times' Markets section RSS -- free, no key, always
    available. Not company-filtered by the publisher (it's a general
    Markets feed), so filtered here by a simple case-insensitive
    substring match against the company name in the title/summary. Only
    matching entries are returned -- an unmatched company genuinely has
    thin coverage in this feed, and substituting unrelated market news
    would corrupt the sentiment signal rather than honestly report that.
    """
    import feedparser

    feed = feedparser.parse(ET_MARKETS_RSS_URL)
    query_lower = query.lower()
    matched = [
        e for e in feed.entries
        if query_lower in f"{e.get('title', '')} {e.get('summary', '')}".lower()
    ]

    return [
        {
            "title": e.get("title"),
            "description": e.get("summary"),
            "source": "Economic Times",
            "published_at": e.get("published") or e.get("updated"),
            "url": e.get("link"),
        }
        for e in matched[:page_size]
    ]


def get_news_debug(query: str, days: int = 7, page_size: int = 10) -> dict:
    """Same NewsAPI call as _get_newsapi_news, but returns its raw
    totalResults alongside the parsed articles -- for diagnosing whether a
    low article count is the API's actual coverage or a client-side
    truncation bug. NewsAPI-specific by design (that's the source this was
    built to debug); use get_indian_news directly to check the Indian path."""
    api_key = os.environ["NEWS_API_KEY"]
    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    resp = requests.get(
        NEWS_API_URL,
        params={
            "q": query, "from": from_date, "sortBy": "relevancy",
            "language": "en", "pageSize": page_size, "apiKey": api_key,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "status": data.get("status"),
        "totalResults": data.get("totalResults"),
        "articles_returned": len(data.get("articles", [])),
    }


if __name__ == "__main__":
    import json
    print("--- debug (NewsAPI) ---")
    print(json.dumps(get_news_debug("Apple Inc", days=7, page_size=20), indent=2))
    print("--- articles (NewsAPI) ---")
    print(json.dumps(get_news("Apple Inc", days=7, page_size=10), indent=2))
    print("--- articles (Indian, routed) ---")
    print(json.dumps(get_news("Punjab National Bank", ticker="PNB.NS", days=7, page_size=10), indent=2))
