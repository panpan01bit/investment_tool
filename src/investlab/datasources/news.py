"""新闻聚合：RSS 抓取 + 观察清单匹配 + LLM 标签（可选）。

保留旧 daily-news-fetcher 的核心思想：
1. feedparser 拉取 sources.config.json 中的源 → data/news/<date>/；
2. 按 watchlist.json 的 ticker/关键词匹配命中 → hits；
3. 区分「24h 焦点」与「30d 背景」供简报使用。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import feedparser

from ..config import get_settings
from ..utils.common import atomic_write_text, now_cn, setup_logging, today_str
from .symbols import split_symbol_column

log = setup_logging("investlab.news")

DEFAULT_SOURCES = [
    {"name": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
    {"name": "36氪", "url": "https://36kr.com/feed"},
    {"name": "Solidot", "url": "https://www.solidot.org/index.rss"},
]

DEFAULT_WATCHLIST = {
    "tickers": [],
    "companyKeywords": {},
    "macroKeywords": [
        "PMI", "CPI", "GDP", "LPR", "降准", "降息", "美联储",
        "AI", "算力", "光模块", "液冷", "数据中心", "HBM", "人形机器人",
    ],
}


def watchlist_path() -> Path:
    return get_settings().data_dir / "watchlist.json"


def load_watchlist() -> dict:
    """优先级：data/watchlist.json（用户管理）→ 投研档案种子 → 持仓文件 → 内置默认。"""
    p = watchlist_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("watchlist.json 损坏，使用投研档案默认")

    # 投研档案（profile）提供与研究主线匹配的种子
    try:
        from ..profiles import load_profile

        prof = load_profile()
        wl = {
            "tickers": list(prof.get("watchlist_seed") or []),
            "companyKeywords": dict(prof.get("company_keywords") or {}),
            "macroKeywords": list(prof.get("macro_keywords") or []),
        }
        if wl["tickers"] or wl["companyKeywords"] or wl["macroKeywords"]:
            return wl
    except Exception as exc:
        log.debug("从投研档案生成 watchlist 失败: %s", exc)

    # 从 Obsidian 组合目录的 holdings.csv 兜底生成 tickers
    wl = json.loads(json.dumps(DEFAULT_WATCHLIST))
    try:
        from ..obsidian.vault import new_vault

        hp = new_vault().holdings_path()
        if hp.is_file():
            tickers = []
            for line in hp.read_text(encoding="utf-8").splitlines()[1:]:
                if line.strip():
                    sym, _ = split_symbol_column(line.split(",")[0])
                    if sym:
                        tickers.append(sym)
            wl["tickers"] = sorted(set(tickers))
    except Exception as exc:
        log.debug("从持仓生成 watchlist 失败: %s", exc)
    return wl


def save_watchlist(wl: dict) -> None:
    p = watchlist_path()
    atomic_write_text(p, json.dumps(wl, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ 抓取


def fetch_all(sources: list[dict] | None = None, *, per_source_limit: int = 40) -> list[dict]:
    """抓取全部源，返回统一 article 列表：{source,title,link,published,summary}。"""
    sources = sources or _load_sources_config()
    articles: list[dict] = []
    for src in sources:
        url = str(src.get("url", "")).strip()
        name = str(src.get("name", src.get("key", "rss")))
        enabled = src.get("enabled", True)
        if not url or not enabled:
            continue
        items = _safe_fetch(url)
        for e in items[:per_source_limit]:
            articles.append(
                {
                    "id": f"{name}:{hash(e.get('link', '')) & 0xFFFFFFFF:x}",
                    "source": name,
                    "title": _clean(e.get("title") or ""),
                    "link": e.get("link") or "",
                    "published": e.get("published"),
                    "summary": _clean(e.get("summary") or ""),
                }
            )
    # 存档
    day_dir = get_settings().news_dir / today_str()
    day_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        day_dir / "raw.json", json.dumps(articles, ensure_ascii=False, indent=1)
    )
    return articles


def _load_sources_config() -> list[dict]:
    """RSS 源优先级：data/sources.config.json（用户管理）→ 投研档案 → 内置默认。"""
    cfg = get_settings().data_dir / "sources.config.json"
    if cfg.is_file():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")) or DEFAULT_SOURCES
        except ValueError:
            pass
    try:
        from ..profiles import load_profile

        sources = load_profile().get("news_sources") or []
        if sources:
            return sources
    except Exception as exc:
        log.debug("从投研档案读取新闻源失败: %s", exc)
    return DEFAULT_SOURCES


def _safe_fetch(url: str) -> list[dict]:
    from ..netguard import UnsafeURLError, validate_url

    try:
        url = validate_url(url)
        parsed = feedparser.parse(url, request_headers={"User-Agent": "investlab/2.0"})
    except (UnsafeURLError, Exception) as exc:  # noqa: B014 — 单源失败不阻塞
        log.debug("RSS 失败 %s: %s", url, exc)
        return []
    out = []
    for e in parsed.entries[:60]:
        published = None
        if getattr(e, "published_parsed", None):
            published = time.strftime("%Y-%m-%dT%H:%M:%S%z", e.published_parsed)
        summary = ""
        if getattr(e, "summary", None):
            summary = re.sub(r"<[^>]+>", "", e.summary)[:400]
        elif getattr(e, "description", None):
            summary = re.sub(r"<[^>]+>", "", e.description)[:400]
        link = getattr(e, "link", "")
        out.append(
            {
                "title": getattr(e, "title", ""),
                "link": link,
                "published": published,
                "summary": summary,
            }
        )
    return out


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or ""))
    return text.strip()


# ------------------------------------------------------------------ 匹配


def match_articles(
    articles: list[dict], watchlist: dict | None = None
) -> dict[str, list[dict]]:
    """按观察清单分组。返回 {ticker或'宏': [article...], '_macro': [...]}。"""
    wl = watchlist or load_watchlist()
    result: dict[str, list[dict]] = {}
    for art in articles:
        blob = f"{art['title']} {art['summary']}"
        for sym_key, kws in (wl.get("companyKeywords") or {}).items():
            kws = kws if isinstance(kws, list) else [kws]
            if any(k and k.lower() in blob.lower() for k in kws):
                result.setdefault(sym_key, []).append(art)
        for tk in wl.get("tickers") or []:
            code6 = re.sub(r"[.\-].*$", "", str(tk))
            if code6 and code6 in blob:
                result.setdefault(str(tk), []).append(art)
        for kw in wl.get("macroKeywords") or []:
            if kw and kw.lower() in blob.lower():
                result.setdefault("_macro", []).append(art)
                break
    return result


def fresh_vs_background(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """24h 内为焦点，30 天内为背景。"""
    now = now_cn()
    fresh, bg = [], []

    def parse_ts(a):
        from ..utils.common import parse_date

        d = parse_date((a.get("published") or "")[:10])
        return d

    import datetime as dt

    for a in hits:
        d = parse_ts(a)
        if not d:
            bg.append(a)
            continue
        age_h = (now - dt.datetime(d.year, d.month, d.day, tzinfo=now.tzinfo)).total_seconds() / 3600
        if age_h <= 24:
            fresh.append(a)
        elif age_h <= 24 * 30:
            bg.append(a)
    return fresh, bg
