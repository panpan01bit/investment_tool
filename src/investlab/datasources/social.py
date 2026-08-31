"""社媒/舆情热度聚合（灵感致谢 last30days-skill：按真实互动量打分，而非编辑权重）。

五个免费无密钥源（各自独立降级，互不阻塞）：
  Reddit      r/ 社区讨论      www.reddit.com/search.json（upvotes+评论数）
  Hacker News 技术圈共识        hn.algolia.com（points+评论数）
  Polymarket  真金下注的赔率    gamma-api.polymarket.com/public-search（成交额+概率）
  StockTwits  交易者情绪        api.stocktwits.com/api/2（Bullish/Bearish 占比，仅美股代码）
  GitHub      开发者活动        api.github.com/search/repositories（stars，可选 GITHUB_TOKEN）

输出 social_pulse()：
  {items:[{source,title,url,metric,engagement,created,extra}], heat:0-100,
   heat_label, source_status:{reddit: ok|no-results|error, ...}}

纪律：每个源独立容错并如实报告状态；全部失败时 heat=None（不编造）。
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from ..netguard import http_get_json
from ..utils.common import cache_get, cache_put, setup_logging
from . import symbols as sym

log = setup_logging("investlab.social")

CACHE_TTL_S = 30 * 60.0
SOURCES = ("reddit", "hn", "polymarket", "stocktwits", "github")

_RE_US_TICKER = re.compile(r"^[A-Z]{1,5}$")


# ------------------------------------------------------------------ 各源实现


def _relevant(title: str, query: str) -> bool:
    """关键词全量匹配过滤（drop 掉 $ticker/泛词后需全部出现在标题中），
    防止 Polymarket 等模糊搜索把"Team Liquid"混进"liquid cooling"。"""
    filler = {"stock", "stocks", "price", "share", "shares", "market"}
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", query.lower())
        if t not in filler and not t.startswith("$")
    ]
    if not tokens:
        return True
    tl = title.lower()
    return all(t in tl for t in tokens)


def _reddit(query: str, since: datetime) -> tuple[list[dict], str]:
    data = http_get_json(
        "https://www.reddit.com/search.json",
        params={"q": query, "sort": "top", "t": "month", "limit": 20},
        timeout=10, retries=0,
        headers={"Accept": "application/json"},
        strict=True,
    )
    children = ((data or {}).get("data") or {}).get("children") or []
    items = []
    for c in children:
        d = c.get("data") or {}
        score = int(d.get("score") or 0)
        title = str(d.get("title") or "")
        if score <= 0 or not _relevant(title, query):
            continue
        items.append({
            "source": "reddit",
            "title": title[:140],
            "url": "https://www.reddit.com" + str(d.get("permalink") or ""),
            "metric": "upvotes",
            "engagement": score,
            "created": datetime.fromtimestamp(
                int(d.get("created_utc") or 0), tz=timezone.utc
            ).isoformat()[:10],
            "extra": {
                "subreddit": d.get("subreddit"),
                "comments": int(d.get("num_comments") or 0),
            },
        })
    items.sort(key=lambda x: -x["engagement"])
    return items[:10], ("ok" if items else "no-results")


def _hn(query: str, since: datetime) -> tuple[list[dict], str]:
    data = http_get_json(
        "https://hn.algolia.com/api/v1/search",
        params={
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{int(since.timestamp())}",
            "hitsPerPage": 15,
        },
        timeout=10, retries=0, strict=True,
    )
    hits = (data or {}).get("hits") or []
    items = []
    for h in hits:
        points = int(h.get("points") or 0)
        title = str(h.get("title") or "")
        if points <= 0 or not _relevant(title, query):
            continue
        items.append({
            "source": "hn",
            "title": title[:140],
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "metric": "points",
            "engagement": points,
            "created": str(h.get("created_at") or "")[:10],
            "extra": {"comments": int(h.get("num_comments") or 0)},
        })
    items.sort(key=lambda x: -x["engagement"])
    return items[:8], ("ok" if items else "no-results")


def _polymarket(query: str, since: datetime) -> tuple[list[dict], str]:
    del since
    data = http_get_json(
        "https://gamma-api.polymarket.com/public-search",
        params={"q": query, "limit_per_type": 8, "events_status": "active"},
        timeout=10, retries=0, strict=True,
    )
    events = (data or {}).get("events") or []
    items = []
    for ev in events:
        title = str(ev.get("title") or "")
        if not _relevant(title, query):
            continue
        markets = ev.get("markets") or []
        volume = _f(ev.get("volume24hr")) or _f(ev.get("volume")) or 0.0
        prob = None
        for m in markets[:1]:
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                try:
                    arr = json_loads(prices)
                    prob = max((float(x) for x in arr), default=None)
                except Exception:
                    prob = None
        if volume <= 0 and prob is None:
            continue
        items.append({
            "source": "polymarket",
            "title": title[:140],
            "url": f"https://polymarket.com/event/{ev.get('slug') or ev.get('id', '')}",
            "metric": "volume24h_usd",
            "engagement": int(volume),
            "created": str(ev.get("startDate") or "")[:10],
            "extra": {"implied_probability": round(prob, 3) if prob is not None else None},
        })
    items.sort(key=lambda x: -x["engagement"])
    return items[:6], ("ok" if items else "no-results")


def _github(query: str, since: datetime) -> tuple[list[dict], str]:
    import os

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = http_get_json(
        "https://api.github.com/search/repositories",
        params={
            "q": f"{query} created:>={since.date().isoformat()}",
            "sort": "stars", "order": "desc", "per_page": 8,
        },
        timeout=10, retries=0, headers=headers, strict=True,
    )
    repos = (data or {}).get("items") or []
    items = []
    for r in repos:
        stars = int(r.get("stargazers_count") or 0)
        full_name = str(r.get("full_name") or "")
        if stars < 5 or not _relevant(full_name + " " + str(r.get("description") or ""), query):
            continue
        items.append({
            "source": "github",
            "title": f"{full_name}: {str(r.get('description') or '')[:90]}",
            "url": r.get("html_url"),
            "metric": "stars",
            "engagement": stars,
            "created": str(r.get("created_at") or "")[:10],
            "extra": {"language": r.get("language")},
        })
    return items[:6], ("ok" if items else "no-results")


def _stocktwits(symbol: str | None) -> tuple[list[dict], str]:
    """仅美股代码可用；返回情绪消息流（Bullish/Bearish 占比）。"""
    if not symbol or not _RE_US_TICKER.match(symbol):
        return [], "skipped(非美股代码)"
    data = http_get_json(
        f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
        timeout=10, retries=0, strict=True,
    )
    messages = (data or {}).get("messages") or []
    labeled = [m for m in messages if (m.get("sentiment") or {}).get("basic")]
    bullish = sum(1 for m in labeled if m["sentiment"]["basic"] == "Bullish")
    items = []
    for m in messages[:8]:
        body = str(m.get("body") or "")[:140]
        if not body:
            continue
        user = (m.get("user") or {}).get("username") or "anon"
        items.append({
            "source": "stocktwits",
            "title": f"@{user}: {body}",
            "url": f"https://stocktwits.com/{user}/message/{m.get('id', '')}",
            "metric": "sentiment",
            "engagement": int(bool((m.get("sentiment") or {}).get("basic"))),
            "created": str(m.get("created_at") or "")[:10],
            "extra": {"basic": (m.get("sentiment") or {}).get("basic")},
        })
    if labeled:
        items.append({
            "source": "stocktwits",
            "title": f"{symbol} 情绪面：{bullish}/{len(labeled)} 条标注为 Bullish"
                     f"（{round(bullish / len(labeled) * 100)}%）",
            "url": f"https://stocktwits.com/symbol/{symbol}",
            "metric": "bullish_pct",
            "engagement": len(messages),
            "created": "",
            "extra": {"bullish_pct": round(bullish / len(labeled) * 100, 1),
                      "labeled": len(labeled)},
        })
    return items, ("ok" if items else "no-results")


# ------------------------------------------------------------------ 聚合


def social_pulse(
    query: str,
    *,
    symbol: str | None = None,
    days: int = 30,
    use_cache: bool = True,
) -> dict:
    """跨源舆情热度。query 建议用英文（Reddit/HN 为英文社区）。"""
    query = (query or "").strip()[:120]
    if not query:
        return _empty_pulse("查询为空")
    norm_symbol = sym.normalize(symbol).split(".")[0] if symbol else None
    cache_key = [query, norm_symbol, days]
    if use_cache:
        cached = cache_get("social_v1", cache_key, CACHE_TTL_S)
        if cached:
            return cached

    since = datetime.now(timezone.utc) - timedelta(days=days)
    status: dict[str, str] = {}
    items: list[dict] = []
    jobs = {
        "reddit": lambda: _reddit(query, since),
        "hn": lambda: _hn(query, since),
        "polymarket": lambda: _polymarket(query, since),
        "github": lambda: _github(query, since),
        "stocktwits": lambda: _stocktwits(
            norm_symbol if (norm_symbol and sym.market_of(norm_symbol) == "US"
                            and _RE_US_TICKER.match(norm_symbol)) else None
        ),
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                src_items, src_status = fut.result()
            except Exception as exc:
                src_items, src_status = [], f"error({str(exc)[:60]})"
            status[name] = src_status
            items.extend(src_items)

    heat = _heat(items, status)
    result = {
        "query": query,
        "symbol": symbol,
        "window_days": days,
        "items": items,
        "heat": heat,
        "heat_label": _heat_label(heat),
        "source_status": status,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if items or any(not s.startswith(("error", "skipped")) for s in status.values()):
        cache_put("social_v1", cache_key, result, ttl_s=CACHE_TTL_S)
    return result


def _heat(items: list[dict], status: dict[str, str]) -> float | None:
    """按源的互动量对数归一到 0-100；所有源均无数据时返回 None。"""
    totals: dict[str, int] = {}
    for it in items:
        totals[it["source"]] = totals.get(it["source"], 0) + int(it.get("engagement") or 0)
    weights = {"reddit": 0.30, "stocktwits": 0.20, "polymarket": 0.20,
               "github": 0.15, "hn": 0.15}
    score = 0.0
    used_w = 0.0
    for src, total in totals.items():
        # log10(1+互动总量)≈0~5+，映射到 0~100
        src_score = min(100.0, math.log10(1 + max(total, 0)) * 22.0)
        w = weights.get(src, 0.1)
        score += src_score * w
        used_w += w
    if used_w <= 0:
        return None
    return round(min(100.0, score / used_w), 1)


def _heat_label(heat: float | None) -> str:
    if heat is None:
        return "无数据"
    if heat >= 80:
        return "白热化"
    if heat >= 60:
        return "火热"
    if heat >= 40:
        return "升温"
    if heat >= 20:
        return "平淡"
    return "冷清"


def _empty_pulse(reason: str) -> dict:
    return {
        "query": "", "symbol": None, "window_days": 30, "items": [],
        "heat": None, "heat_label": "无数据",
        "source_status": {"all": f"skipped({reason})"},
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def json_loads(s: str):
    import json

    return json.loads(s)


def pulse_for_symbol(symbol: str) -> dict:
    """标的 → 社媒查询构造：美股/港股用裸代码（英文社区标题常用 ticker，
    填充词反而拉低相关性过滤命中）；A股代码在英文社区命中低，如实返回 no-results。"""
    s = sym.normalize(symbol)
    base = s.split(".")[0].lstrip("0") if s.endswith(".HK") else s.split(".")[0]
    market = sym.market_of(s)
    if market == "US":
        q = base
    elif market == "HK":
        q = f"{base}.HK"
    else:
        q = base
    return social_pulse(q, symbol=s)
