"""
News tool -- pulls recent headlines/articles for a company via NewsAPI's
free-tier 'everything' endpoint. Kept deliberately dumb (just fetch + shape
the response); sentiment scoring lives in sentiment.py so this module has
exactly one job and is easy to swap for a different news provider later.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

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
    from_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

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


if __name__ == "__main__":
    import json
    print(json.dumps(get_news("Apple", days=7, page_size=5), indent=2))
