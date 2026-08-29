"""
News tool -- pulls recent headlines/articles for a company via NewsAPI's
free-tier 'everything' endpoint. Kept deliberately dumb (just fetch + shape
the response); sentiment scoring lives in sentiment.py so this module has
exactly one job and is easy to swap for a different news provider later.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

NEWS_API_URL = "https://newsapi.org/v2/everything"


def get_news(query: str, days: int = 7, page_size: int = 10) -> list[dict]:
    """
    Recent news for a company/ticker name. `query` should be the company
    name (e.g. "Apple"), not the ticker -- NewsAPI matches on article text,
    and tickers alone produce noisy/irrelevant results.
    """
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

    if data.get("totalResults", 0) > len(data.get("articles", [])):
        # NewsAPI's free tier sometimes reports more totalResults than it
        # actually returns in `articles` -- worth knowing about rather than
        # silently truncating, since it affects how much signal the News
        # Analyst agent actually has to work with.
        pass  # visible via get_news_debug below, not raised as an error

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


def get_news_debug(query: str, days: int = 7, page_size: int = 10) -> dict:
    """Same call, but returns NewsAPI's raw totalResults alongside the
    parsed articles -- for diagnosing whether a low article count is the
    API's actual coverage or a client-side truncation bug."""
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
    print("--- debug ---")
    print(json.dumps(get_news_debug("Apple Inc", days=7, page_size=20), indent=2))
    print("--- articles ---")
    print(json.dumps(get_news("Apple Inc", days=7, page_size=10), indent=2))
