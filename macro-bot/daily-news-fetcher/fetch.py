#!/usr/bin/env python3
"""Zero-dependency daily RSS fetcher for server. Saves raw news + watchlist hits.

Usage:
    python3 fetch.py [--dry-run]

Output:
    news/YYYY-MM-DD.json   all fetched articles
    hits/YYYY-MM-DD.json   articles matched against watchlist
    logs/YYYY-MM-DD.log    fetch log
"""

import argparse
import json
import os
import re
import ssl
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib import request
from xml.etree import ElementTree as ET

# Default TLS verification is ON. Individual sources can opt out via verify_ssl:false.

def _create_ssl_context(verify: bool = True) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "news")
HITS_DIR = os.path.join(BASE_DIR, "hits")
LOG_DIR = os.path.join(BASE_DIR, "logs")
SOURCES_PATH = os.path.join(BASE_DIR, "sources.config.json")
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")

MAX_ITEMS_PER_SOURCE = 30
FETCH_TIMEOUT_SEC = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.0 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0"
)


def mkdir(p: str) -> None:
    if not os.path.exists(p):
        os.makedirs(p, exist_ok=True)


def read_json(p: str) -> Any:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def strip_html(html: str) -> str:
    if not html:
        return ""
    s = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.I)
    s = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&\w+;", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def fetch_url(url: str, source: Optional[Dict[str, Any]] = None) -> str:
    verify = source.get("verify_ssl", True) if source else True
    req = request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/xml,text/xml,*/*",
        },
    )
    ctx = _create_ssl_context(verify=verify)
    with request.urlopen(req, timeout=FETCH_TIMEOUT_SEC, context=ctx) as resp:
        data = resp.read()
    # Try utf-8 first; fall back to common Chinese encodings
    for enc in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def parse_rss(xml: str) -> List[Dict[str, Any]]:
    items = []
    # ElementTree can choke on invalid XML; sanitize obvious issues
    xml = re.sub(r"<\?xml[^?]*\?>", "", xml, count=1)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        # Fallback: regex extract
        item_re = re.compile(r"<item>([\s\S]*?)</item>", re.I)
        for block in item_re.findall(xml):
            title = re.search(r"<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\])?</title>", block, re.I)
            link = re.search(r"<link>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\])?</link>", block, re.I)
            pub = re.search(r"<pubDate>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\])?</pubDate>", block, re.I)
            desc = re.search(r"<description>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\])?</description>", block, re.I)
            content = re.search(r"<content:encoded>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\])?</content:encoded>", block, re.I)
            if title or link:
                items.append({
                    "title": strip_html(title.group(1) if title else ""),
                    "url": strip_html(link.group(1) if link else ""),
                    "publishedAt": parse_pub_date(pub.group(1) if pub else ""),
                    "excerpt": strip_html(content.group(1) if content else (desc.group(1) if desc else ""))[:500],
                })
        return items[:MAX_ITEMS_PER_SOURCE]

    # Use namespace-agnostic iteration
    channel = root.find("channel") or root
    for item in channel.findall("item"):
        def get(tag: str) -> str:
            el = item.find(tag)
            return el.text or "" if el is not None else ""
        title = get("title")
        link = get("link")
        pub = get("pubDate")
        desc = get("description")
        content = get("{http://purl.org/rss/1.0/modules/content/}encoded")
        if not title and not link:
            continue
        items.append({
            "title": strip_html(title),
            "url": strip_html(link),
            "publishedAt": parse_pub_date(pub),
            "excerpt": strip_html(content or desc)[:500],
        })
    return items[:MAX_ITEMS_PER_SOURCE]


def parse_pub_date(raw: str) -> Optional[str]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        # Most RSS dates are RFC-2822; fromisoformat handles a subset
        return datetime.strptime(raw[:25], "%a, %d %b %Y %H:%M:%S").isoformat()
    except Exception:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
        except Exception:
            return None


def load_watchlist() -> Dict[str, Any]:
    w = read_json(WATCHLIST_PATH)
    ticker_set: Set[str] = set(w.get("tickers", []))
    macro = [str(k).lower() for k in w.get("macroKeywords", [])]
    company = {}
    for t, kw in w.get("companyKeywords", {}).items():
        company[t] = [str(k).lower() for k in kw]
    return {
        "tickerSet": ticker_set,
        "macro": macro,
        "company": company,
        "forceAnalyze": set(w.get("forceAnalyze", [])),
    }


def is_keyword_match(text: str, keyword: str) -> bool:
    """Keyword match with word-boundary guard for short / ascii keywords.
    Chinese keywords are matched as substring because Chinese has no word spaces.
    """
    keyword = keyword.lower().strip()
    if not keyword:
        return False
    text_lower = text.lower()
    # Chinese / long keywords (>= 5 chars) can use substring match
    if len(keyword) >= 5 or any(ord(c) > 127 for c in keyword):
        return keyword in text_lower
    # Short ascii keywords require word boundaries to avoid false substring hits
    # e.g. KER matching inside 'broker', PUM inside 'compute', ONON inside 'ondon'
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text_lower))


def detect_hits(article: Dict[str, Any], watchlist: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = f"{article.get('title', '')} {article.get('excerpt', '')} {article.get('url', '')}".lower()
    hits = {"tickers": [], "macro": [], "companies": []}

    for t in watchlist["tickerSet"]:
        if is_keyword_match(text, t):
            hits["tickers"].append(t)

    for m in watchlist["macro"]:
        if is_keyword_match(text, m):
            hits["macro"].append(m)

    for t in watchlist["tickerSet"]:
        kws = watchlist["company"].get(t, [])
        for k in kws:
            if is_keyword_match(text, k):
                hits["companies"].append(t)
                break

    # 单独扫描所有配置了 companyKeywords 的 ticker（即使不在 tickerSet）
    for t, kws in watchlist["company"].items():
        if t in watchlist["tickerSet"]:
            continue
        for k in kws:
            if is_keyword_match(text, k):
                hits["companies"].append(t)
                break

    hits["tickers"] = list(set(hits["tickers"]))
    hits["macro"] = list(set(hits["macro"]))
    hits["companies"] = list(set(hits["companies"]))
    total = len(hits["tickers"]) + len(hits["macro"]) + len(hits["companies"])
    has_company_or_ticker = len(hits["tickers"]) > 0 or len(hits["companies"]) > 0
    if not has_company_or_ticker:
        return None
    return {**article, "hits": hits, "hitScore": total}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="do not write output files")
    args = parser.parse_args()

    mkdir(OUT_DIR)
    mkdir(HITS_DIR)
    mkdir(LOG_DIR)

    sources = [s for s in read_json(SOURCES_PATH) if s.get("enabled") is not False]
    watchlist = load_watchlist()
    date = today_str()
    log_file = os.path.join(LOG_DIR, f"{date}.log")
    log_lines = [f"=== {datetime.now().isoformat()} ===", f"Sources: {len(sources)}"]

    all_articles: List[Dict[str, Any]] = []
    source_stats: List[Dict[str, Any]] = []

    for source in sources:
        try:
            xml = fetch_url(source["url"], source=source)
            items = parse_rss(xml)
            articles = [
                {
                    **it,
                    "sourceId": source["id"],
                    "sourceName": source["name"],
                    "category": source.get("category", "news"),
                    "fetchedAt": datetime.now().isoformat(),
                }
                for it in items
            ]
            all_articles.extend(articles)
            source_stats.append({"id": source["id"], "count": len(articles), "status": "ok"})
            log_lines.append(f"[OK] {source['id']}: {len(articles)} items")
            print(f"[OK] {source['id']:20} {len(articles)} items")
        except Exception as e:
            msg = str(e)
            source_stats.append({"id": source["id"], "count": 0, "status": "failed", "error": msg})
            log_lines.append(f"[ERR] {source['id']}: {msg}")
            print(f"[ERR] {source['id']:20} {msg}")

    report = {
        "date": date,
        "fetchedAt": datetime.now().isoformat(),
        "totalArticles": len(all_articles),
        "sources": source_stats,
        "articles": all_articles,
    }

    hits = [h for h in (detect_hits(a, watchlist) for a in all_articles) if h]
    hits.sort(key=lambda x: x["hitScore"], reverse=True)

    hits_report = {
        "date": date,
        "fetchedAt": datetime.now().isoformat(),
        "totalHits": len(hits),
        "watchlist": {
            "tickers": list(watchlist["tickerSet"]),
            "macroKeywords": watchlist["macro"],
            "forceAnalyze": list(watchlist["forceAnalyze"]),
        },
        "hits": hits,
    }

    if not args.dry_run:
        with open(os.path.join(OUT_DIR, f"{date}.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        with open(os.path.join(HITS_DIR, f"{date}.json"), "w", encoding="utf-8") as f:
            json.dump(hits_report, f, ensure_ascii=False, indent=2)
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        print(f"\nWrote {OUT_DIR}/{date}.json ({len(all_articles)} articles)")
        print(f"Wrote {HITS_DIR}/{date}.json ({len(hits)} hits)")
    else:
        print(f"\n[DRY-RUN] would write {len(all_articles)} articles, {len(hits)} hits")

    print("\nTop 5 hits:")
    for i, h in enumerate(hits[:5], 1):
        print(
            f"  {i}. [{h.get('category')}] {h.get('title')} — "
            f"tickers:{','.join(h['hits']['tickers']) or '-'} "
            f"macro:{','.join(h['hits']['macro']) or '-'} "
            f"companies:{','.join(h['hits']['companies']) or '-'}"
        )


if __name__ == "__main__":
    main()
