"""News fetcher for macro-bot.

Tries to read pre-aggregated daily news hits from the daily-news-fetcher
output directory first; falls back to FinanceMCP if the file is missing.
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DAILY_NEWS_DIR = os.path.join(BASE_DIR, "daily-news-fetcher", "hits")
TAGS_DIR = os.path.join(BASE_DIR, "daily-news-fetcher", "tags")
WATCHLIST_PATH = os.path.join(BASE_DIR, "daily-news-fetcher", "watchlist.json")
DEFAULT_NEWS_HOURS_WINDOW = 24
DEFAULT_NEWS_MAX_RESULTS = 5
BACKGROUND_DAYS = 30

A_SHARE_EXCHANGES = {"SS", "SZ", "BJ", "SH"}


class NewsItems(list):
    """Formatted news items carrying the number verified as fresh."""

    fresh_count: int = 0


TICKER_MARKET_ALIASES = {
    # tagger/watchlist 后缀 -> holdings 后缀（以及反向）
    "SS": "CH", "SH": "CH", "SZ": "CH", "BJ": "CH",
    "T": "JP", "HK": "HK", "DE": "GR", "PA": "FR",
    "SW": "SW", "US": "US", "L": "UK",
}


def _ticker_equivalents(code: str) -> set:
    """给定一个裸代码（如 '688169'）或带后缀的 ticker，返回所有可能的等效 ticker 集合。"""
    if not code:
        return set()
    base = code.split(".")[0]
    suffix = code.split(".")[1].upper() if "." in code else ""
    equiv = {base}
    for sfx, mkt in TICKER_MARKET_ALIASES.items():
        if sfx == suffix or mkt == suffix or not suffix:
            equiv.add(f"{base}.{sfx}")
            equiv.add(f"{base}.{mkt}")
    return {x.upper() for x in equiv}


def _load_company_keywords() -> Dict[str, List[str]]:
    """Load company keyword aliases from watchlist.json for fuzzy matching."""
    if not os.path.exists(WATCHLIST_PATH):
        return {}
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("companyKeywords", {})
    except Exception:
        return {}


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_tags_file(date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    date = date or _today_str()
    path = os.path.join(TAGS_DIR, f"{date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_hits_file(date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    date = date or _today_str()
    path = os.path.join(DAILY_NEWS_DIR, f"{date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _word_match(text: str, kw: str) -> bool:
    kw = kw.lower().strip()
    if not kw:
        return False
    if len(kw) >= 5 or any(ord(c) > 127 for c in kw):
        return kw in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text))


def _match_score(article: Dict[str, Any], holding: Dict[str, Any]) -> int:
    """Score an article against a holding using the pre-computed hit metadata.

    Falls back to title/excerpt keyword matching if hit metadata is missing.
    """
    ticker = (holding.get("ticker") or "").upper()
    exchange = (holding.get("exchange") or "").upper()
    full_ticker = f"{ticker}.{exchange}" if exchange else ticker
    ticker_equiv = _ticker_equivalents(full_ticker)
    short_name = (holding.get("short_name") or "").strip()

    score = 0
    article_hits = article.get("hits", {})
    for hit_ticker in article_hits.get("tickers", []):
        hit_ticker = hit_ticker.upper()
        if ticker_equiv & _ticker_equivalents(hit_ticker):
            score += 10
    for company_hit in article_hits.get("companies", []):
        company_hit = company_hit.upper()
        if ticker_equiv & _ticker_equivalents(company_hit):
            score += 5

    if score > 0:
        if article.get("category") == "a-share-notice":
            score += 3
        elif article.get("category") == "a-share-notice-old":
            score += 1

    if score == 0:
        text = (f"{article.get('title', '')} {article.get('excerpt', '')}").lower()
        if _word_match(text, full_ticker) or _word_match(text, ticker):
            score += 10
        if short_name and _word_match(text, short_name):
            score += 5
        company_keywords = _load_company_keywords()
        for key in (full_ticker, ticker):
            for kw in company_keywords.get(key, []):
                if _word_match(text, kw):
                    score += 5
                    break

    return score


def _format_article(article: Dict[str, Any]) -> str:
    title = article.get("title", "").strip()
    url = article.get("url", "").strip()
    source = article.get("sourceName", "").strip()
    if not title:
        return ""
    if url and source:
        return f"{title} [{source}] {url}"
    elif url:
        return f"{title} {url}"
    return title


def _parse_date(s: str) -> Optional[datetime]:
    """Parse a date string in various formats."""
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _split_by_recency(
    articles: List[Dict[str, Any]],
    hours: int = 24,
    background_days: int = BACKGROUND_DAYS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split articles into "last N hours" and "older context".

    Articles older than `background_days` are dropped so the prompt does not get
    stale information. Articles without a parseable date are treated as
    background because we cannot verify their recency.
    """
    now = datetime.now()
    cutoff = now - timedelta(hours=hours)
    background_cutoff = now - timedelta(days=background_days)
    fresh, background = [], []
    for a in articles:
        dt = _parse_date(a.get("publishedAt", ""))
        if dt is None:
            background.append(a)
            continue
        if dt >= cutoff:
            fresh.append(a)
        elif dt >= background_cutoff:
            background.append(a)
    return fresh, background


def _dedupe_candidates(candidates: List[Dict[str, Any]], max_items: int) -> List[Dict[str, Any]]:
    seen_urls: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for a in candidates:
        url = a.get("url", "").strip()
        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        out.append(a)
        if len(out) >= max_items:
            break
    return out


def fetch_news_for_holding(
    holding: Dict[str, Any],
    hours_window: int = DEFAULT_NEWS_HOURS_WINDOW,
    max_results: int = DEFAULT_NEWS_MAX_RESULTS,
    date: Optional[str] = None,
) -> List[str]:
    """Fetch news for a single holding using LLM tags as the primary source.

    Falls back to keyword-based hits if LLM tags are unavailable.
    Returns a list of formatted strings ordered so that the first items are the
    last-24h focus and later items are recent background context.
    """
    ticker = (holding.get("ticker") or "").upper()
    exchange = (holding.get("exchange") or "").upper()
    full_ticker = f"{ticker}.{exchange}" if exchange else ticker
    ticker_equiv = _ticker_equivalents(full_ticker)

    candidates: List[Dict[str, Any]] = []

    # Primary: LLM tags
    tags_data = _load_tags_file(date)
    if tags_data is not None:
        for tag in tags_data.get("tags", []):
            equiv_set = set().union(*(_ticker_equivalents(c) for c in tag.get("companies", [])))
            if ticker_equiv & equiv_set:
                candidates.append({
                    "title": tag.get("title", ""),
                    "url": tag.get("url", ""),
                    "sourceName": tag.get("sourceName", ""),
                    "excerpt": "",
                    "publishedAt": tag.get("publishedAt", ""),
                    "category": tag.get("category", ""),
                })

    # Fallback: keyword-based hits
    if not candidates:
        hits_data = _load_hits_file(date)
        if hits_data is not None:
            scored = []
            for article in hits_data.get("hits", []):
                s = _match_score(article, holding)
                if s > 0:
                    scored.append((s, article))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates = [a for _, a in scored]

    # Source 2: FinanceMCP legacy news (holding-specific) with short timeout, appended last
    mcp_candidates: List[Dict[str, Any]] = []
    try:
        import socket
        from mcp_client import get_recent_news

        original_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(2.0)
            sn = holding.get("short_name") or holding.get("ticker", "")
            mcp_news = get_recent_news(sn, limit=max_results * 2) or []
            for n in mcp_news:
                text = str(n)
                if not text:
                    continue
                url = ""
                if text.startswith("http") or "http" in text:
                    parts = text.split()
                    url = parts[-1] if parts[-1].startswith("http") else ""
                mcp_candidates.append({
                    "title": text,
                    "url": url,
                    "sourceName": "FinanceMCP",
                    "excerpt": "",
                    "publishedAt": "",
                    "category": "mcp-news",
                })
        finally:
            socket.setdefaulttimeout(original_timeout)
    except Exception:
        pass

    all_candidates = _dedupe_candidates(candidates + mcp_candidates, max_items=max_results * 3)

    fresh, background = _split_by_recency(all_candidates, hours=hours_window)

    formatted: List[str] = NewsItems()
    for a in fresh[:max_results]:
        formatted.append(_format_article(a))
    formatted.fresh_count = len(formatted)
    remaining = max_results - len(formatted)
    if remaining > 0 and background:
        for a in background[:remaining]:
            formatted.append(_format_article(a))

    return formatted


def format_news_for_prompt(news_items: List[str]) -> str:
    """Format a list of news strings for inclusion in the Kimi prompt.

    The caller is expected to pass items ordered so that the first N items are
    the last-24h focus; the rest are recent background.
    """
    if not news_items:
        return "暂无新闻。"
    fresh_count = getattr(news_items, "fresh_count", len(news_items))
    focus_count = min(3, fresh_count or len(news_items))
    lines = []
    if fresh_count:
        lines.append("【今日焦点（过去24小时，请重点分析）】")
    else:
        lines.append("【近期相关报道（24h 内未抓到，仅供参考）】")
    for i, t in enumerate(news_items[:focus_count], 1):
        lines.append(f"{i}. {t}")
    if len(news_items) > focus_count:
        lines.append("")
        lines.append(f"【近{BACKGROUND_DAYS}日背景（可作为补充参考，请勿作为主要分析对象）】")
        for i, t in enumerate(news_items[focus_count:], focus_count + 1):
            lines.append(f"{i}. {t}")
    return "\n".join(lines)
