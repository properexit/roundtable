"""
News sentiment via Azure AI Language (Text Analytics) -- this is the piece
that makes the project's Azure usage about AI services, not just hosting.

Deliberately returns an aggregate (mean positive/neutral/negative across all
articles + a simple net score) rather than per-article scores, because the
News/Sentiment Analyst agent needs one clear signal to reason over, not a
list it has to average itself.
"""
from __future__ import annotations

import os

from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential

MAX_DOC_LEN = 5120  # Azure Text Analytics per-document character limit


def _client() -> TextAnalyticsClient:
    endpoint = os.environ["AZURE_LANGUAGE_ENDPOINT"]
    key = os.environ["AZURE_LANGUAGE_KEY"]
    return TextAnalyticsClient(endpoint=endpoint, credential=AzureKeyCredential(key))


def analyze_news_sentiment(articles: list[dict]) -> dict:
    """
    articles: output of news.get_news(). Concatenates title + description
    per article (truncated to Azure's per-doc limit) and returns an
    aggregate sentiment signal.
    """
    if not articles:
        return {"overall_sentiment": "neutral", "confidence": {}, "article_count": 0}

    documents = [
        f"{a['title']}. {a.get('description') or ''}"[:MAX_DOC_LEN]
        for a in articles
    ]

    client = _client()
    results = client.analyze_sentiment(documents)

    pos = neu = neg = 0.0
    valid = 0
    for r in results:
        if r.is_error:
            continue
        pos += r.confidence_scores.positive
        neu += r.confidence_scores.neutral
        neg += r.confidence_scores.negative
        valid += 1

    if valid == 0:
        return {"overall_sentiment": "neutral", "confidence": {}, "article_count": 0}

    avg = {"positive": pos / valid, "neutral": neu / valid, "negative": neg / valid}
    overall = max(avg, key=avg.get)

    return {
        "overall_sentiment": overall,
        "confidence": {k: round(v, 3) for k, v in avg.items()},
        "net_score": round(avg["positive"] - avg["negative"], 3),
        "article_count": valid,
    }


if __name__ == "__main__":
    import json
    from src.tools.news import get_news

    articles = get_news("Apple", days=7, page_size=5)
    print(json.dumps(analyze_news_sentiment(articles), indent=2))
