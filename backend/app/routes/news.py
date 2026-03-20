from fastapi import APIRouter
from fastapi.responses import JSONResponse
import httpx
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta
import pytz
import os
import httpx as _httpx
import json as _json

router = APIRouter()

RSS_FEEDS = [
    {"source": "Economic Times Markets",
     "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"source": "Economic Times Economy",
     "url": "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms"},
    {"source": "Moneycontrol",
     "url": "https://www.moneycontrol.com/rss/latestnews.xml"},
    {"source": "Yahoo Finance India",
     "url": "https://finance.yahoo.com/rss/topfinstories"},
    {"source": "Yahoo Finance",
     "url": "https://finance.yahoo.com/news/rssindex"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_news_cache: list       = []
_news_cache_ts: datetime = datetime.utcnow() - timedelta(minutes=10)
NEWS_CACHE_TTL = 300  # refresh every 5 minutes

def parse_age(date_str: str) -> str:
    try:
        import email.utils
        pub = email.utils.parsedate_to_datetime(date_str)
        now = datetime.now(pytz.utc)
        diff = now - pub
        mins = int(diff.total_seconds() / 60)
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except Exception:
        return "recently"

def parse_rss_xml(xml_text: str, source: str) -> list:
    articles = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item")
        for item in items[:4]:
            title = item.findtext("title", "").strip()
            title = re.sub(r"<[^>]+>", "", title)
            desc = item.findtext("description", "").strip()
            desc = re.sub(r"<[^>]+>", "", desc)[:200]
            link = item.findtext("link", "").strip()
            pub = item.findtext("pubDate", "")
            if title:
                articles.append({
                    "title":     title,
                    "summary":   desc,
                    "source":    source,
                    "published": parse_age(pub),
                    "link":      link,
                })
    except Exception as e:
        print(f"[NEWS] XML parse error {source}: {e}")
    return articles

def fetch_news() -> list:
    articles = []
    with httpx.Client(headers=HEADERS, timeout=10, follow_redirects=True,
                      verify=False) as client:
        for feed_meta in RSS_FEEDS:
            try:
                resp = client.get(feed_meta["url"])
                items = parse_rss_xml(resp.text, feed_meta["source"])
                print(f"[NEWS] {feed_meta['source']}: {len(items)} entries")
                articles.extend(items)
            except Exception as e:
                print(f"[NEWS] ERROR {feed_meta['source']}: {e}")
    print(f"[NEWS] Total: {len(articles)}")
    return articles[:12]

@router.get("/api/news")
def get_news():
    global _news_cache, _news_cache_ts
    now = datetime.utcnow()
    if _news_cache and (now - _news_cache_ts).total_seconds() < NEWS_CACHE_TTL:
        return JSONResponse(content=_news_cache)
    _news_cache    = fetch_news()
    _news_cache_ts = now
    return JSONResponse(content=_news_cache)


_sentiment_cache:    dict     = {}
_sentiment_cache_ts: datetime = datetime.utcnow() - timedelta(minutes=30)
SENTIMENT_TTL = 900  # regenerate every 15 minutes

@router.get("/api/sentiment")
async def get_sentiment():
    global _sentiment_cache, _sentiment_cache_ts
    now = datetime.utcnow()
    if _sentiment_cache and (now - _sentiment_cache_ts).total_seconds() < SENTIMENT_TTL:
        return JSONResponse(content=_sentiment_cache)

    # Get latest headlines to feed to Claude
    headlines = [a["title"] for a in fetch_news()[:8]]
    headlines_text = "\n".join(f"- {h}" for h in headlines)

    prompt = f"""You are a financial market analyst. Based on these real market headlines, 
generate a brief market sentiment analysis. Respond ONLY in this exact JSON format with 
no markdown, no backticks, no extra text:
{{
  "overall": "Bullish" or "Bearish" or "Neutral",
  "summary": "One sentence summary of market mood (max 12 words)",
  "confidence": 65,
  "notes": "Two to three sentence analyst note about key themes in the news"
}}

Headlines:
{headlines_text}"""

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise Exception("ANTHROPIC_API_KEY not set")
            
        async with _httpx.AsyncClient(timeout=20) as client:
            res = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-3-5-sonnet-20241022",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            res.raise_for_status()
        data = res.json()
        text = data["content"][0]["text"].strip()
        sentiment = _json.loads(text)
    except Exception as e:
        sentiment = {
            "overall":    "Neutral",
            "summary":    "Market data temporarily unavailable",
            "confidence": 50,
            "notes":      "Unable to generate AI sentiment at this time.",
        }

    _sentiment_cache    = sentiment
    _sentiment_cache_ts = now
    return JSONResponse(content=sentiment)
