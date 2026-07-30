#!/usr/bin/env python3
"""AKShare A-share news fetcher for macro-bot.

Pulls A-share announcements, 7x24 market news, sector boards, and macro data
for tickers in the watchlist. Outputs:
    - hits/YYYY-MM-DD.json   (same format as fetch.py, articles merged with foreign RSS hits)
    - akshare/YYYY-MM-DD.json (raw structured data for debugging)

Designed to be invoked after fetch.py or standalone. Run as:
    python3.8 akshare_fetcher.py
"""

import argparse
import json
import os
import re
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import akshare as ak

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HITS_DIR = os.path.join(BASE_DIR, "hits")
RAW_DIR = os.path.join(BASE_DIR, "akshare")
LOG_DIR = os.path.join(BASE_DIR, "logs")
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")

MAX_NOTICES_PER_STOCK = 10
MAX_RECENT_DAYS = 3          # calendar days for stock_notice_report (today + previous 2)
MAX_OLD_NOTICES_PER_STOCK = 5
MAX_MARKET_NEWS = 200
MAX_BOARD_ROWS = 15
DEFAULT_DAYS_BACK = 30


def mkdir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def read_json(p: str) -> Any:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p: str, data: Any) -> None:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_watchlist() -> Dict[str, Any]:
    """Load watchlist and return only A-share tickers."""
    w = read_json(WATCHLIST_PATH)
    a_tickers = []
    for t in w.get("tickers", []):
        if re.match(r"^\d{6}\.(SS|SZ|BJ)$", t.upper()):
            a_tickers.append(t)
    for t in w.get("companyKeywords", {}).keys():
        if re.match(r"^\d{6}\.(SS|SZ|BJ)$", t.upper()) and t not in a_tickers:
            a_tickers.append(t)
    return {
        "tickers": a_tickers,
        "companyKeywords": w.get("companyKeywords", {}),
        "macroKeywords": w.get("macroKeywords", []),
        "forceAnalyze": w.get("forceAnalyze", []),
    }


def get_stock_info(symbol: str) -> Dict[str, str]:
    """Best-effort get stock short name and industry."""
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
        info = dict(zip(df["item"].astype(str), df["value"].astype(str)))
        return {
            "name": info.get("股票简称", ""),
            "industry": info.get("行业", ""),
        }
    except Exception as e:
        return {"name": "", "industry": ""}


def _code_to_ticker(code: str) -> str:
    """Map 6-digit code to .SZ/.SS/.BJ based on exchange rules."""
    code = str(code).strip()
    if not re.match(r"^\d{6}$", code):
        return ""
    if code.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return f"{code}.SS"
    if code.startswith(("000", "001", "002", "003", "200", "300", "301", "430")):
        return f"{code}.SZ"
    if code.startswith(("8", "4", "43")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _fetch_daily_notices_cache(
    watchlist: Dict[str, Any],
    days_back: int = MAX_RECENT_DAYS,
    cache: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Fetch all-market notices for recent calendar dates and return a
    dict mapping date (YYYY-MM-DD) -> DataFrame plus log lines.

    Cached so that one call per date is made regardless of how many tickers
    are in the watchlist.
    """
    cache = cache or {}
    log_lines: List[str] = []
    now = datetime.now()
    target_codes = {t.split(".")[0] for t in watchlist["tickers"]}
    for offset in range(days_back):
        d = now - timedelta(days=offset)
        date_str = d.strftime("%Y%m%d")
        date_key = d.strftime("%Y-%m-%d")
        if date_key in cache:
            continue
        try:
            df = ak.stock_notice_report(symbol="全部", date=date_str)
            if df is None or df.empty:
                cache[date_key] = None
                log_lines.append(f"[DAILY_NOTICE] {date_key}: 0 rows")
                continue
            # Keep only watchlist tickers
            df = df[df["代码"].astype(str).isin(target_codes)].copy()
            cache[date_key] = df
            log_lines.append(f"[DAILY_NOTICE] {date_key}: {len(df)} rows for watchlist")
        except Exception as e:
            traceback.print_exc()
            cache[date_key] = None
            log_lines.append(f"[DAILY_NOTICE] {date_key}: ERROR {e}")
    return cache, log_lines


def fetch_recent_notices(
    watchlist: Dict[str, Any],
    cache: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fetch recent official announcements for the watchlist from East Money."""
    cache, log_lines = _fetch_daily_notices_cache(watchlist, days_back=MAX_RECENT_DAYS, cache=cache)
    items: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for date_key, df in cache.items():
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            name = str(row.get("名称", "")).strip()
            title = str(row.get("公告标题", "")).strip()
            notice_type = str(row.get("公告类型", "")).strip()
            notice_date = str(row.get("公告日期", "")).strip()
            url = str(row.get("网址", "")).strip()
            ticker = _code_to_ticker(code)
            if not ticker:
                continue
            key = f"{ticker}|{title}|{notice_date}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            full_title = f"{name}({ticker}):{title}" if name else title
            items.append({
                "title": full_title,
                "url": url,
                "publishedAt": notice_date,
                "excerpt": notice_type,
                "sourceName": "东方财富公告",
                "category": "a-share-notice",
                "symbols": [ticker],
                "affected": [ticker],
            })
    return items, log_lines


def fetch_old_notices(symbol: str, days_back: int = DEFAULT_DAYS_BACK) -> List[Dict[str, Any]]:
    """Fetch older announcements from CNINFO via AKShare as a fallback."""
    try:
        end = datetime.now()
        start = end - timedelta(days=days_back)
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        items = []
        for _, row in df.head(MAX_OLD_NOTICES_PER_STOCK).iterrows():
            items.append({
                "title": str(row.get("公告标题", "")),
                "url": str(row.get("公告链接", "")),
                "publishedAt": str(row.get("公告时间", "")),
                "excerpt": "",
                "sourceName": "巨潮资讯",
                "category": "a-share-notice-old",
                "symbols": [symbol],
                "affected": [symbol],
            })
        return items
    except Exception as e:
        traceback.print_exc()
        return []


def fetch_market_news() -> List[Dict[str, Any]]:
    """Fetch East Money 7x24 market news and convert to article format."""
    try:
        df = ak.stock_news_em()
        if df is None or df.empty:
            return []
        df = df.head(MAX_MARKET_NEWS)
        items = []
        for _, row in df.iterrows():
            items.append({
                "title": str(row.get("新闻标题", "")),
                "excerpt": str(row.get("新闻内容", ""))[:500],
                "url": str(row.get("新闻链接", "")),
                "publishedAt": str(row.get("发布时间", "")),
                "sourceName": str(row.get("文章来源", "")),
                "category": "a-share-news",
                "symbols": [str(row.get("关键词", ""))],
                "affected": [],
            })
        return items
    except Exception as e:
        traceback.print_exc()
        return []


def fetch_sector_boards() -> Dict[str, List[Dict[str, Any]]]:
    """Fetch industry and concept board movers."""
    result = {}
    try:
        for name, fn in [("industry", ak.stock_board_industry_name_em),
                          ("concept", ak.stock_board_concept_name_em)]:
            df = fn()
            if df is None or df.empty:
                continue
            rows = []
            for _, row in df.head(MAX_BOARD_ROWS).iterrows():
                rows.append({
                    "name": str(row.get("板块名称", "")),
                    "change_pct": str(row.get("涨跌幅", "")),
                    "top_stock": str(row.get("领涨股票", "")),
                    "top_stock_change": str(row.get("领涨股票-涨跌幅", "")),
                })
            result[name] = rows
    except Exception as e:
        traceback.print_exc()
    return result


def fetch_macro() -> Dict[str, Any]:
    """Fetch latest China macro indicators."""
    result = {}
    try:
        pmi = ak.macro_china_pmi().head(1)
        if not pmi.empty:
            row = pmi.iloc[0]
            result["pmi"] = {
                "month": str(row.get("月份", "")),
                "manufacturing": str(row.get("制造业-指数", "")),
                "non_manufacturing": str(row.get("非制造业-指数", "")),
            }
    except Exception as e:
        traceback.print_exc()
    try:
        cpi = ak.macro_china_cpi().head(1)
        if not cpi.empty:
            row = cpi.iloc[0]
            result["cpi"] = {
                "month": str(row.get("月份", "")),
                "national_yoy": str(row.get("全国-同比增长", "")),
                "national_mom": str(row.get("全国-环比增长", "")),
            }
    except Exception as e:
        traceback.print_exc()
    try:
        gdp = ak.macro_china_gdp().head(1)
        if not gdp.empty:
            row = gdp.iloc[0]
            result["gdp"] = {
                "quarter": str(row.get("季度", "")),
                "value": str(row.get("国内生产总值-绝对值", "")),
                "yoy": str(row.get("国内生产总值-同比增长", "")),
            }
    except Exception as e:
        traceback.print_exc()
    try:
        lpr = ak.macro_china_lpr()
        lpr = lpr.dropna(subset=["LPR1Y", "LPR5Y"], how="all").tail(1)
        if not lpr.empty:
            row = lpr.iloc[0]
            result["lpr"] = {
                "date": str(row.get("TRADE_DATE", "")),
                "lpr_1y": str(row.get("LPR1Y", "")),
                "lpr_5y": str(row.get("LPR5Y", "")),
            }
    except Exception as e:
        traceback.print_exc()
    return result


def detect_hits(article: Dict[str, Any], watchlist: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Same hit detection logic as fetch.py but adapted for A-share data."""
    text = f"{article.get('title', '')} {article.get('excerpt', '')} {article.get('url', '')}".lower()
    hits = {"tickers": [], "macro": [], "companies": []}

    # Honor explicitly attached affected symbols (e.g. from stock_notice_report)
    for aff in article.get("affected", []):
        aff = str(aff).upper()
        if aff in watchlist["tickers"]:
            if aff not in hits["tickers"]:
                hits["tickers"].append(aff)

    for t in watchlist["tickers"]:
        sym = t.split(".")[0]
        # Exact ticker match (e.g. 002444)
        if t not in hits["tickers"] and (sym in text or t.lower() in text):
            hits["tickers"].append(t)
        # Company keywords
        for kw in watchlist["companyKeywords"].get(t, []):
            kw = str(kw).lower()
            if len(kw) >= 5 or any(ord(c) > 127 for c in kw):
                if kw in text:
                    hits["companies"].append(t)
                    break
            else:
                if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text):
                    hits["companies"].append(t)
                    break

    for m in watchlist["macroKeywords"]:
        m = str(m).lower()
        if len(m) >= 5 or any(ord(c) > 127 for c in m):
            if m in text:
                hits["macro"].append(m)
        else:
            if re.search(r"(?<![a-z0-9])" + re.escape(m) + r"(?![a-z0-9])", text):
                hits["macro"].append(m)

    hits["tickers"] = list(set(hits["tickers"]))
    hits["macro"] = list(set(hits["macro"]))
    hits["companies"] = list(set(hits["companies"]))

    # For market news, also use the pre-attached keyword symbol as a ticker hit
    for sym in article.get("symbols", []):
        if sym and re.match(r"^\d{6}$", sym):
            for t in watchlist["tickers"]:
                if t.startswith(sym):
                    if t not in hits["tickers"]:
                        hits["tickers"].append(t)
                    if t not in hits["companies"]:
                        hits["companies"].append(t)

    total = len(hits["tickers"]) + len(hits["macro"]) + len(hits["companies"])
    has_company_or_ticker = len(hits["tickers"]) > 0 or len(hits["companies"]) > 0
    if not has_company_or_ticker:
        return None
    return {**article, "hits": hits, "hitScore": total}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="do not write output files")
    parser.add_argument("--date", default=None, help="override date (YYYY-MM-DD)")
    args = parser.parse_args()

    mkdir(HITS_DIR)
    mkdir(RAW_DIR)
    mkdir(LOG_DIR)

    date = args.date or today_str()
    watchlist = load_watchlist()
    log_lines = [f"=== AKShare fetch {datetime.now().isoformat()} ===", f"A-share tickers: {len(watchlist['tickers'])}"]

    all_articles: List[Dict[str, Any]] = []
    stock_infos: Dict[str, Dict[str, str]] = {}

    # 1. Recent official announcements (today + past 2 calendar days)
    notice_cache: Dict[str, Any] = {}
    recent_notices, recent_logs = fetch_recent_notices(watchlist, cache=notice_cache)
    log_lines.extend(recent_logs)
    log_lines.append(f"[NOTICE] recent (stock_notice_report): {len(recent_notices)} items")
    all_articles.extend(recent_notices)

    # 2. Older official announcements as fallback / context
    for t in watchlist["tickers"]:
        sym = t.split(".")[0]
        old_notices = fetch_old_notices(sym)
        stock_infos[t] = get_stock_info(sym)
        log_lines.append(f"[NOTICE] {t}: {len(old_notices)} older items")
        all_articles.extend(old_notices)

    # 3. Market-wide 7x24 news
    market_news = fetch_market_news()
    log_lines.append(f"[NEWS] market 7x24: {len(market_news)} items")
    all_articles.extend(market_news)

    # 4. Sector boards and macro data
    sector_boards = fetch_sector_boards()
    macro = fetch_macro()
    log_lines.append(f"[BOARD] industries: {len(sector_boards.get('industry', []))}, concepts: {len(sector_boards.get('concept', []))}")
    log_lines.append(f"[MACRO] indicators: {list(macro.keys())}")

    raw_report = {
        "date": date,
        "fetchedAt": datetime.now().isoformat(),
        "totalArticles": len(all_articles),
        "stockInfo": stock_infos,
        "sectorBoards": sector_boards,
        "macro": macro,
        "articles": all_articles,
    }

    hits = [h for h in (detect_hits(a, watchlist) for a in all_articles) if h]
    hits.sort(key=lambda x: x["hitScore"], reverse=True)
    # Tie-break: put more recent official announcements first, then market news
    def _sort_key(h):
        cat = h.get("category", "")
        # official recent notices first, then older notices, then market news
        cat_order = 0 if cat == "a-share-notice" else (1 if cat == "a-share-notice-old" else 2)
        return (cat_order, -h.get("hitScore", 0))
    hits.sort(key=_sort_key)

    hits_report = {
        "date": date,
        "fetchedAt": datetime.now().isoformat(),
        "totalHits": len(hits),
        "watchlist": {
            "tickers": watchlist["tickers"],
            "macroKeywords": watchlist["macroKeywords"],
            "forceAnalyze": watchlist["forceAnalyze"],
        },
        "hits": hits,
    }

    if not args.dry_run:
        write_json(os.path.join(RAW_DIR, f"{date}.json"), raw_report)
        # Merge with existing hits file if present
        existing_path = os.path.join(HITS_DIR, f"{date}.json")
        if os.path.exists(existing_path):
            try:
                existing = read_json(existing_path)
                existing_hits = existing.get("hits", [])
                seen_urls = {h.get("url") for h in existing_hits if h.get("url")}
                merged = existing_hits[:]
                for h in hits:
                    if h.get("url") and h.get("url") in seen_urls:
                        continue
                    seen_urls.add(h.get("url"))
                    merged.append(h)
                merged.sort(key=lambda x: (0 if x.get("category") == "a-share-notice" else (1 if x.get("category") == "a-share-notice-old" else 2), -x.get("hitScore", 0)))
                existing["totalHits"] = len(merged)
                existing["hits"] = merged
                existing["fetchedAt"] = datetime.now().isoformat()
                existing["akshareMerged"] = True
                write_json(existing_path, existing)
                log_lines.append(f"[MERGE] updated {existing_path} with {len(hits)} new hits, total {len(merged)}")
            except Exception as e:
                traceback.print_exc()
                write_json(existing_path, hits_report)
                log_lines.append(f"[WRITE] wrote {existing_path} (merge failed, fresh)")
        else:
            write_json(existing_path, hits_report)
            log_lines.append(f"[WRITE] wrote {existing_path} ({len(hits)} hits)")

        log_file = os.path.join(LOG_DIR, f"{date}.akshare.log")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines))
        print(f"Wrote {RAW_DIR}/{date}.json ({len(all_articles)} articles)")
        print(f"Wrote {HITS_DIR}/{date}.json ({len(hits)} hits)")
    else:
        print(f"[DRY-RUN] {len(all_articles)} articles, {len(hits)} hits")

    print("\nTop 10 AKShare hits:")
    for i, h in enumerate(hits[:10], 1):
        print(f"  {i}. [{h.get('category')}] {h.get('title')} — tickers:{','.join(h['hits']['tickers']) or '-'} macro:{','.join(h['hits']['macro']) or '-'} companies:{','.join(h['hits']['companies']) or '-'}")


if __name__ == "__main__":
    main()
