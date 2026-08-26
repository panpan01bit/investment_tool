"""宏观一页纸：PMI / CPI / GDP / LPR + 板块热度。

优先读本地 daily-news-fetcher 产物（data/news/akshare/<date>.json，兼容旧目录结构），
缺失时在线拉 akshare。输出人类可读的一页文字 + 结构化数字。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ..config import get_settings
from ..utils.common import cache_get, cache_put, setup_logging, today_str

log = setup_logging("investlab.macro")

MACRO_TTL_S = 6 * 3600.0


def get_macro_summary(*, use_cache: bool = True) -> dict:
    """返回 {text: str, items: {pmi:..., cpi_yoy:..., gdp_yoy:..., lpr_1y:...}, source}。"""
    if use_cache:
        cached = cache_get("macro_v1", ["summary"], MACRO_TTL_S)
        if cached:
            return cached
    result = _macro_from_local() or {}
    if not result.get("items"):
        result = _macro_from_akshare()
    # 附带热门板块（仅在线）
    sectors = _safe_sectors()
    if sectors:
        result["sectors"] = sectors
    cache_put("macro_v1", ["summary"], result, ttl_s=MACRO_TTL_S)
    return result


def format_one_pager(macro: dict) -> str:
    """把宏观 dict 渲染成简报用的一段话。"""
    items = macro.get("items") or {}
    parts = []
    label_map = [
        ("pmi", "制造业PMI"),
        ("cpi_yoy", "CPI同比"),
        ("gdp_yoy", "GDP同比"),
        ("lpr_1y", "1年期LPR"),
        ("m2_yoy", "M2同比"),
    ]
    for key, label in label_map:
        v = items.get(key)
        if v is not None:
            unit = "%" if key != "pmi" else ""
            parts.append(f"{label} {v}{unit}")
    lines = []
    if parts:
        lines.append("宏观：" + "，".join(parts) + f"（{macro.get('source', '')}）")
    if macro.get("sectors"):
        sec = macro["sectors"][:5]
        lines.append("板块热度：" + "、".join(f"{s['name']} {s['change_pct']:+.2f}%" for s in sec))
    if not lines:
        lines.append("宏观：今日暂无可用数据（离线或源不可达）")
    return "\n".join(lines)


# ------------------------------------------------------------------ 本地文件


def _local_files_candidates() -> list:
    import json
    from pathlib import Path

    news_dir = Path(get_settings().news_dir)
    out = []
    d = date.today()
    for i in range(3):
        day = (d - timedelta(days=i)).isoformat()
        p = news_dir / "akshare" / f"{day}.json"
        if p.is_file():
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    legacy = Path("daily-news-fetcher/akshare")
    for i in range(3):
        day = (d - timedelta(days=i)).isoformat()
        p = legacy / f"{day}.json"
        if p.is_file():
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


def _extract_number(text, *patterns: str):
    for pat in patterns:
        m = re.search(pat, text if isinstance(text, str) else "")
        if m:
            try:
                return float(m.group(1))
            except (IndexError, ValueError):
                continue
    return None


def _macro_from_local() -> dict | None:
    for blob in _local_files_candidates():
        text_blob = blob.get("macro") or {}
        flat = (
            "".join(str(v) for v in text_blob.values())
            if isinstance(text_blob, dict)
            else str(text_blob)
        )
        flat += "".join(
            str(it.get("title", "")) + str(it.get("summary", ""))
            for it in blob.get("hits", [])[:50]
            if isinstance(it, dict)
        )
        items = {
            "pmi": _extract_number(flat, r"PMI[为\s:]*(\d+\.?\d*)"),
            "cpi_yoy": _extract_number(flat, r"CPI[^0-9\-]{0,10}(\-?\d+\.?\d*)%"),
            "gdp_yoy": _extract_number(flat, r"GDP[^0-9\-]{0,10}(\d+\.?\d*)%"),
            "lpr_1y": _extract_number(flat, r"1年期LPR[为\s]*(\d+\.?\d*)%?"),
            "m2_yoy": _extract_number(flat, r"M2[^0-9\-]{0,6}(\d+\.?\d*)%"),
        }
        items = {k: v for k, v in items.items() if v is not None}
        if items:
            return {"items": items, "source": "本地新闻抓取", "ts": today_str()}
    return None


# ------------------------------------------------------------------ 在线 akshare


def _macro_from_akshare() -> dict:
    try:
        import akshare as ak
    except ImportError:
        return {"items": {}, "source": "akshare 未安装", "ts": today_str()}
    items: dict[str, float] = {}
    source_bits = []
    try:
        pmi_df = ak.macro_china_pmi()
        items["pmi"] = round(float(pmi_df.iloc[-1, 1]), 1)
        source_bits.append("akshare PMI")
    except Exception as exc:
        log.debug("PMI 获取失败: %s", exc)
    try:
        cpi_df = ak.macro_china_cpi_monthly()
        items["cpi_yoy"] = round(float(cpi_df.iloc[-1, 1]), 2)
    except Exception as exc:
        log.debug("CPI 获取失败: %s", exc)
    try:
        lpr_df = ak.macro_china_lpr()
        cols = list(lpr_df.columns)
        rate_col = cols[-1] if len(cols) > 1 else cols[0]
        items["lpr_1y"] = round(float(lpr_df.iloc[-1][rate_col]), 2)
    except Exception as exc:
        log.debug("LPR 获取失败: %s", exc)
    return {
        "items": items,
        "source": "+".join(source_bits) or "akshare",
        "ts": today_str(),
    }


def _safe_sectors() -> list[dict]:
    from .quotes import _safe_call

    rows = _safe_call(_sector_board_em) or []
    out = []
    for name, pct in rows[:8]:
        out.append({"name": str(name), "change_pct": round(float(pct), 2)})
    return out


def _sector_board_em():
    """东财行业板块即时行情 [(名称, 涨跌幅)]。"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        pairs = list(zip(df["板块名称"], df["涨跌幅"], strict=False))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs
    except Exception as exc:
        log.debug("板块行情失败: %s", exc)
        return []
