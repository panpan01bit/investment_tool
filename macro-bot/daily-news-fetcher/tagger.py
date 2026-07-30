#!/usr/bin/env python3
"""LLM-based article tagger for daily-news-fetcher.

Reads news/YYYY-MM-DD.json, tags each article with relevant watchlist companies,
sectors and macro themes, and writes tags/YYYY-MM-DD.json.

Usage:
    python3 tagger.py [YYYY-MM-DD]
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib import request

# Load env from the bot's .env file if present
for env_path in ["/www/wwwroot/macro-bot/.env", os.path.join(os.path.dirname(__file__), ".env")]:
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k not in os.environ:
                        os.environ[k] = v.strip().strip('"').strip("'")
        except Exception:
            pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NEWS_DIR = os.path.join(BASE_DIR, "news")
TAGS_DIR = os.path.join(BASE_DIR, "tags")
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")

KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_API_URL = os.getenv("KIMI_API_URL", "https://api.moonshot.cn/v1/chat/completions")
KIMI_MODEL = os.getenv("KIMI_TAGGER_MODEL", "kimi-k2.6")
BATCH_SIZE = int(os.getenv("KIMI_TAGGER_BATCH", "1"))
MAX_WORKERS = int(os.getenv("KIMI_TAGGER_WORKERS", "8"))

# Sector → watchlist tickers mapping. related_companies is derived automatically from sectors.
# Keep narrow; avoid broad catch-all sectors like "technology" or "consumer discretionary".
SECTOR_TO_TICKERS: Dict[str, List[str]] = {
    "sportswear": ["PUM.DE", "ONON"],
    "luxury": ["KER.PA", "UHR.SW"],
    "toys": ["9992.HK"],
    "retail": ["WMT", "COST", "MNSO"],
    "hotels": ["ATAT"],
    "travel": ["ATAT", "9843.T"],
    "beauty": ["4911.T", "300866.SZ"],
    "coffee": ["LKNCY"],
    "food & beverage": ["LKNCY"],
    "robotics": ["688169.SS"],
    "social media": ["SN"],
    "tools": ["002444.SZ"],
    "hardware": ["002444.SZ"],
    "airbnb": ["9843.T"],
    "walmart": ["WMT"],
    "costco": ["COST"],
    "miniso": ["MNSO"],
    "pop mart": ["9992.HK"],
    "puma": ["PUM.DE"],
    "on running": ["ONON"],
    "kering": ["KER.PA"],
    "swatch": ["UHR.SW"],
    "shiseido": ["4911.T"],
    "roborock": ["688169.SS"],
    "snap": ["SN"],
    "greatstar": ["002444.SZ"],
    "atour": ["ATAT"],
    "luckin": ["LKNCY"],
}


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_watchlist() -> Dict[str, Any]:
    w = load_json(WATCHLIST_PATH)
    return {
        "tickerSet": set(w.get("tickers", [])),
        "companyKeywords": w.get("companyKeywords", {}),
        "macroKeywords": w.get("macroKeywords", []),
    }


def build_tag_prompt(articles: List[Dict[str, Any]], watchlist: Dict[str, Any]) -> str:
    company_list = []
    for ticker, kws in watchlist["companyKeywords"].items():
        name = kws[0] if kws else ticker
        company_list.append(f"{ticker} ({name})")
    company_lines = "\n".join(company_list)

    macro_list = "\n".join(watchlist["macroKeywords"])

    article_lines = []
    for i, a in enumerate(articles, 1):
        title = a.get("title", "")
        excerpt = a.get("excerpt", "")
        source = a.get("sourceName", "")
        article_lines.append(
            f"[{i}] TITLE: {title}\n    SOURCE: {source}\n    EXCERPT: {excerpt[:300]}"
        )

    return f"""You are a strict financial news tagging assistant. For each article, decide which watchlist companies it directly names and which sectors it belongs to.

WATCHLIST COMPANIES (ticker format: ticker (common names/aliases)):
{company_lines}

SECTORS TO CHOOSE FROM (use only these or their close variants):
- sportswear
- luxury
- toys
- retail
- consumer discretionary
- hotels
- travel
- beauty
- coffee
- food & beverage
- technology
- robotics
- social media
- tools
- hardware
- airbnb

MACRO KEYWORDS TO CONSIDER:
{macro_list}

TASK:
For each article, output a JSON object with:
- "companies": list of watchlist tickers that the article DIRECTLY names or is clearly about. ONLY direct mentions/earnings/products. If none, return [].
- "sectors": list of sector strings from the sector list above that the article is meaningfully about. Keep them concise and lowercase.
- "macro": list of macro themes from the macro keywords that the article materially discusses. If none, return [].
- "relevance": float 0-1 indicating how directly relevant this article is to any watchlist company.
- "reason": one sentence explaining the tagging decision.

SCORING RULES:
1. relevance >= 0.7: the article directly names a watchlist company (e.g., earnings, product, contract). Put ticker in "companies".
2. relevance 0.4-0.6: the article is about a sector that contains watchlist companies (e.g., "Nike in China" for sportswear; "luxury demand" for luxury). No tickers in "companies".
3. relevance 0.1-0.3: tangential or broad macro. No tickers in "companies".
4. relevance 0.0: no connection. All lists empty.

EXAMPLES:
- "Nike to cut off online distributors in China" → companies=[], sectors=["sportswear"], macro=[], relevance=0.5
- "Pop Mart Labubu sales surge in Europe" → companies=["9992.HK"], sectors=["toys", "retail"], macro=[], relevance=0.95
- "Oil prices jump as Iran tensions escalate" → companies=[], sectors=[], macro=["原油", "油价"], relevance=0.2
- "Apple teams up with Klarna to launch a lease-to-own program" → companies=[], sectors=[], macro=[], relevance=0.0 (technology is too broad; not tied to a watchlist tech company)
- "Walmart to expand same-day delivery" → companies=["WMT"], sectors=["retail"], macro=[], relevance=0.9
- "Snap settles social media addiction case" → companies=["SN"], sectors=["social media"], macro=[], relevance=0.9
- "Luckin reports quarterly profit" → companies=["LKNCY"], sectors=["coffee"], macro=[], relevance=0.9

OUTPUT FORMAT: Return ONLY a JSON array. No markdown, no code fences, no explanations. Each element matches the article index.

ARTICLES:
{chr(10).join(article_lines)}
"""


def call_kimi_for_tags(prompt: str) -> Optional[List[Dict[str, Any]]]:
    if not KIMI_API_KEY:
        print("[ERR] KIMI_API_KEY not set", file=sys.stderr)
        return None

    payload = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise financial news tagger. Return only JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 1.0,
    }
    headers = {
        "Authorization": f"Bearer {KIMI_API_KEY}",
        "Content-Type": "application/json",
    }

    req = request.Request(
        KIMI_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    print(f"[DEBUG] calling Kimi model={KIMI_MODEL} prompt_len={len(prompt)}", file=sys.stderr)
    start = time.time()
    try:
        with request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            elapsed = time.time() - start
            print(f"[DEBUG] Kimi returned in {elapsed:.1f}s", file=sys.stderr)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content.rsplit("\n", 1)[0]
            content = content.strip()
            return json.loads(content)
    except Exception as e:
        elapsed = time.time() - start
        print(f"[ERR] Kimi call failed after {elapsed:.1f}s: {e}", file=sys.stderr)
        if hasattr(e, 'read'):
            try:
                print(f"[ERR] response body: {e.read().decode()[:500]}", file=sys.stderr)
            except Exception:
                pass
        raise


def derive_related_companies(sectors: List[str], direct_companies: List[str]) -> List[str]:
    """Derive related watchlist tickers from article sectors, excluding direct companies."""
    related = set()
    for sector in sectors:
        sector_key = sector.strip().lower()
        # Only exact match to avoid broad "technology" or "consumer discretionary" catching all
        if sector_key in SECTOR_TO_TICKERS:
            related.update(SECTOR_TO_TICKERS[sector_key])
    related = related - set(direct_companies)
    return sorted(related)


def direct_company_matches(article: Dict[str, Any], watchlist: Dict[str, Any]) -> List[str]:
    """Return watchlist tickers directly mentioned in article title/excerpt via keyword matching."""
    text = (article.get("title", "") + " " + article.get("excerpt", "")).lower()
    matches = []
    for ticker, keywords in watchlist.get("companyKeywords", {}).items():
        for kw in keywords:
            kw_lower = kw.lower()
            # For Chinese or short keywords, use substring; for English, use word boundary when sensible
            if len(kw_lower) > 3 and any(c.isalpha() for c in kw):
                # Word boundary for multi-letter English terms
                pattern = r"(?<![a-z0-9])" + re.escape(kw_lower) + r"(?![a-z0-9])"
                if re.search(pattern, text):
                    matches.append(ticker)
                    break
            else:
                if kw_lower in text:
                    matches.append(ticker)
                    break
    return matches


def tag_articles(articles: List[Dict[str, Any]], watchlist: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tag all articles in batches with parallel workers."""
    tagged = [None] * len(articles)

    def process_batch(start: int) -> Tuple[int, List[Dict[str, Any]]]:
        batch = articles[start:start + BATCH_SIZE]
        prompt = build_tag_prompt(batch, watchlist)
        try:
            tags = call_kimi_for_tags(prompt)
            if tags is None or len(tags) != len(batch):
                print(f"[WARN] batch {start // BATCH_SIZE + 1}: tag count mismatch, using empty tags", file=sys.stderr)
                tags = [{"companies": [], "sectors": [], "macro": [], "relevance": 0.0, "reason": "tagger failed"} for _ in batch]
        except Exception as e:
            print(f"[ERR] batch {start // BATCH_SIZE + 1}: {e}", file=sys.stderr)
            tags = [{"companies": [], "sectors": [], "macro": [], "relevance": 0.0, "reason": f"tagger error: {e}"} for _ in batch]
        return start, tags

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_batch, i) for i in range(0, len(articles), BATCH_SIZE)]
        for future in as_completed(futures):
            start, tags = future.result()
            for idx, (article, tag) in enumerate(zip(articles[start:start + BATCH_SIZE], tags)):
                direct = direct_company_matches(article, watchlist)
                llm_companies = [c for c in tag.get("companies", []) if c in watchlist["tickerSet"]]
                valid_companies = sorted(set(direct) | set(llm_companies))
                sectors = list(set(tag.get("sectors", [])))
                related_companies = derive_related_companies(sectors, valid_companies)
                relevance = float(tag.get("relevance", 0.0))
                if valid_companies and relevance < 0.7:
                    relevance = 0.85
                tagged[start + idx] = {
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "sourceName": article.get("sourceName", ""),
                    "publishedAt": article.get("publishedAt"),
                    "companies": valid_companies,
                    "related_companies": related_companies,
                    "sectors": sectors,
                    "macro": list(set(tag.get("macro", []))),
                    "relevance": relevance,
                    "reason": str(tag.get("reason", "")),
                }

    return tagged


def main() -> None:
    date = sys.argv[1] if len(sys.argv) > 1 else today_str()
    news_path = os.path.join(NEWS_DIR, f"{date}.json")
    tags_path = os.path.join(TAGS_DIR, f"{date}.json")

    if not os.path.exists(news_path):
        print(f"[ERR] news file not found: {news_path}", file=sys.stderr)
        sys.exit(1)

    news_data = load_json(news_path)
    articles = news_data.get("articles", [])
    print(f"[INFO] tagging {len(articles)} articles for {date}")

    watchlist = load_watchlist()
    tagged = tag_articles(articles, watchlist)

    report = {
        "date": date,
        "taggedAt": datetime.now().isoformat(),
        "totalArticles": len(articles),
        "totalTagged": len(tagged),
        "tags": tagged,
    }
    save_json(tags_path, report)
    print(f"[OK] wrote {tags_path} ({len(tagged)} tags)")


if __name__ == "__main__":
    main()
