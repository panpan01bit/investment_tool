"""互联网搜索聚合：DuckDuckGo（免费内置）+ Tavily（可选 token）。

结果统一格式：{title, url, snippet, source_provider, badge}
badge: "[web]" 标记网页搜索结果，与官方行情数据区分显示。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..config import get_settings
from ..utils.common import cache_get, cache_put, setup_logging

log = setup_logging("investlab.search")

SEARCH_TTL_S = 30 * 60.0  # 搜索结果缓存 30 分钟


class SearchHit(dict):
    pass


def search(query: str, *, max_results: int | None = None, use_cache: bool = True) -> list[SearchHit]:
    """聚合并去重排序。无可用 provider 返回空列表。"""
    query = (query or "").strip()
    if not query:
        return []
    s = get_settings()
    n = max_results or s.search_max_results
    key = [query, n]
    if use_cache:
        cached = cache_get("search_v1", key, SEARCH_TTL_S)
        if cached is not None:
            return [SearchHit(h) for h in cached]

    hits: list[SearchHit] = []
    if s.tavily_api_key:
        hits += _safe(_tavily_search, query, s.tavily_api_key, max(n, 5))
    hits += _safe(_ddg_search, query, max(n, 8))
    hits = dedupe(hits)[:n]
    if hits:
        cache_put("search_v1", key, list(map(dict, hits)), ttl_s=SEARCH_TTL_S)
    return hits


# ------------------------------------------------------------------ providers


def _tavily_search(query: str, api_key: str, n: int) -> list[SearchHit]:
    from ..netguard import http_post_json

    data = http_post_json(
        "https://api.tavily.com/search",
        {
            "api_key": api_key,
            "query": query,
            "max_results": min(n, 20),
            "include_answer": False,
            "search_depth": "basic",
        },
        timeout=20,
        retries=0,
    )
    out = []
    for r in (data or {}).get("results") or []:
        out.append(
            SearchHit(
                title=str(r.get("title") or "")[:200],
                url=str(r.get("url") or ""),
                snippet=_strip(str(r.get("content") or ""))[:300],
                provider="tavily",
                badge="[web]",
            )
        )
    return out


def _ddg_search(query: str, n: int) -> list[SearchHit]:
    """DuckDuckGo 免费搜索（ddgs 库，零密钥）。"""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # 老包名兼容
        except ImportError:
            log.debug("未安装 ddgs，跳过 DuckDuckGo")
            return []
    out: list[SearchHit] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=min(n * 2, 25)):
                out.append(
                    SearchHit(
                        title=str(r.get("title") or "")[:200],
                        url=str(r.get("href") or r.get("url") or ""),
                        snippet=_strip(str(r.get("body") or ""))[:300],
                        provider="duckduckgo",
                        badge="[web]",
                    )
                )
    except Exception as exc:
        log.debug("DDG 搜索失败: %s", exc)
    return out


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        log.debug("%s 失败: %s", fn.__name__, exc)
        return []


# ------------------------------------------------------------------ helpers


def _strip(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return url[:60]


TRUSTED_DOMAINS_BONUS = {
    # 高质量财经域名的加权（简单启发式）
    "caixin.com", "yicai.com", "cls.cn", "stcn.com", "21jingji.com",
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com",
    "eastmoney.com", "sina.com.cn", "finance.sina.com.cn", "163.com",
    "xueqiu.com", "caixin.cn", "sec.gov", "hkexnews.hk", "cninfo.com.cn",
}


def dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    """URL 规范化去重 + 域名多样性 + 权威域名加权。"""
    seen_url: set[str] = set()
    seen_domain_count: dict[str, int] = {}
    scored = []
    for h in hits:
        raw = h.get("url", "")
        dom = domain_of(raw)
        norm = f"{dom}{''.join(sorted(re.findall(r'[a-zA-Z0-9]{4,}', raw.lower())))[:80]}"
        if norm in seen_url or not raw.startswith(("http://", "https://")):
            continue
        seen_url.add(norm)
        score = 0.0
        if dom in TRUSTED_DOMAINS_BONUS:
            score += 2.0
        cnt = seen_domain_count.get(dom, 0)
        score -= cnt * 0.8          # 同一域名越多次序越低，保证来源多样
        seen_domain_count[dom] = cnt + 1
        scored.append((score, h))
    scored.sort(key=lambda x: -x[0])
    return [h for _, h in scored]


def format_hits_context(hits: list[SearchHit], limit_chars: int = 2500) -> str:
    """把搜索结果渲染成 LLM prompt 素材。"""
    lines = []
    total = 0
    for i, h in enumerate(hits, 1):
        line = f"{i}. {h['badge']} {h['title']}\n   {h['url']}\n   {h['snippet']}"
        total += len(line)
        if total > limit_chars:
            break
        lines.append(line)
    return "\n".join(lines)
