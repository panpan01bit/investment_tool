"""每日简报（听涛晨报）：原 Feishu 推送流程 → 本地 Obsidian + JSON。

两阶段流水线（对齐 UZI 原则）：
  阶段一（确定性计算，Python）：
    持仓行情+信号 / 宏观一页纸 / 新闻焦点与背景 / 赛道映射
    → 结构化 payload（data/briefings/<date>.json）
  阶段二（LLM 叙述，受 JSON 约束且必须引用数字）：
    → 写 Obsidian「10 听涛日报/YYYY-MM-DD 听涛晨报.md」

无 LLM / 断网时阶段一照常输出（纯数据版笔记），绝不静默丢功能。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import get_settings
from ..datasources import macro as macromod
from ..datasources import news as newsmod
from ..datasources.quotes import get_quotes
from ..llm.client import get_llm
from ..obsidian.vault import build_frontmatter, new_vault
from ..quant.portfolio import load_holdings
from ..quant.signals import compute_signals
from ..tracks import tracks_for_symbol
from ..utils.common import setup_logging, today_str, write_json

log = setup_logging("investlab.briefing")


def run_daily(*, use_cache: bool = True, fetch_news: bool = True) -> dict:
    date_str = today_str()
    settings = get_settings()

    # ---------- 阶段一：确定性取数 ----------
    holdings = load_holdings()
    symbols = [h["symbol"] for h in holdings]
    quotes = get_quotes(symbols, use_cache=use_cache)

    positions = []
    for h in holdings:
        q = quotes.get(h["symbol"], {})
        track_hits = tracks_for_symbol(h["symbol"])
        # 技术信号分（缓存内计算，失败静默降级）
        try:
            sig = compute_signals(h["symbol"], use_cache=use_cache)
            sig_score, sig_stance = sig.score, sig.stance
        except Exception as exc:
            log.debug("信号失败 %s: %s", h["symbol"], exc)
            sig_score, sig_stance = None, "-"
        positions.append({
            **{k: h[k] for k in ("symbol", "name", "quantity", "cost_price", "category")},
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "quote_source": q.get("source", ""),
            "signal_score": sig_score,
            "signal_stance": sig_stance,
            "tracks": [t["name"] for t in track_hits][:3],
        })

    macro = macromod.get_macro_summary(use_cache=use_cache)
    macro_text = macromod.format_one_pager(macro)

    news_section = {"fresh": [], "background": [], "by_symbol": {}}
    if fetch_news:
        try:
            articles = newsmod.fetch_all()
            grouped = newsmod.match_articles(articles)
            fresh, bg = newsmod.fresh_vs_background(
                grouped.get("_macro", []) + articles[:200]
            )
            seen_links = set()
            for a in (fresh or [])[:12]:
                if a["link"] not in seen_links:
                    news_section["fresh"].append(_news_digest(a))
                    seen_links.add(a["link"])
            for a in (bg or [])[:8]:
                if a["link"] not in seen_links:
                    news_section["background"].append(_news_digest(a))
                    seen_links.add(a["link"])
            for sym, arts in grouped.items():
                if sym == "_macro":
                    continue
                news_section["by_symbol"][sym] = [
                    _news_digest(a) for a in arts[:4]
                ]
        except Exception as exc:
            log.warning("新闻环节失败（继续生成简报）: %s", exc)
            news_section["error"] = str(exc)[:120]

    payload = {
        "date": date_str,
        "positions": positions,
        "macro": {**macro, "text": macro_text},
        "news": news_section,
        "thesis": _thesis_line(),
        "ts": today_str("%Y-%m-%d %H:%M:%S"),
    }

    out_json = settings.briefings_dir / f"{date_str}.json"
    write_json(out_json, payload)

    # ---------- 阶段二：叙述 ----------
    narrative = llm_narrative(payload)
    note_path = render_note(_vault := new_vault(), payload, narrative)

    return {
        "date": date_str,
        "payload": payload,
        "narrative_used_llm": narrative is not None,
        "briefing_json": str(out_json),
        "obsidian_note": note_path,
    }


def _news_digest(a: dict) -> dict:
    return {
        "source": a.get("source") or "",
        "title": a.get("title") or "",
        "link": a.get("link") or "",
        "published": (a.get("published") or "")[:19],
    }


def _thesis_line() -> str:
    try:
        from ..tracks import thesis_one_liner

        return thesis_one_liner()
    except Exception:  # taxonomy 损坏不阻塞简报
        return ""


# ------------------------------------------------------------------ LLM 叙述


NARRATIVE_PROMPT = """你是买方研究员的晨报撰写助手。基于下面**确定性数据**（真实数字），写一份中文晨报「听涛晨报」。
要求：
1. 开头 ≤80 字的「今日要点」（必须引用具体涨跌数字或宏观值）；
2. 「持仓诊断」表格后的每条点评 ≤60 字，结合信号分与赛道归属说明关注点；
3. 「宏观」段直接使用给定数值；
4. 「新闻焦点」挑 2-3 条最相关事件各配一句话解读；
5. 结尾给「今日三件事」（可执行动作，如"跟踪光模块板块量能"）。
禁止编造数据里没有的数字；数据缺失就说“暂缺”。

数据：
<briefing>
{json}
</briefing>

只输出 Markdown 正文（不要 frontmatter，不要一级标题）。"""


def llm_narrative(payload: dict) -> str | None:
    llm = get_llm()
    if llm is None:
        return None
    prompt = NARRATIVE_PROMPT.replace("{json}", json.dumps(payload, ensure_ascii=False, default=str))
    try:
        resp = llm.think(prompt, temperature=0.4, max_tokens=2400)
    except Exception as exc:
        log.warning("晨报叙述失败（降级为纯数据版）: %s", exc)
        return None
    text = resp.text.strip()
    if not text:
        return None
    return text


def fallback_narrative(payload: dict) -> str:
    """纯本地模板叙述（离线兜底）。"""
    lines = []
    quotes_bits = [
        f"{p['name'] or p['symbol']} {p['change_pct']:+.2f}%" if p.get("change_pct") is not None else f"{p['name'] or p['symbol']} 行情暂缺"
        for p in payload["positions"][:6]
    ]
    if quotes_bits:
        lines.append("**今日要点**：" + "；".join(quotes_bits) + "。（纯数据版：未配置 LLM 叙述）")
    lines.append("")
    lines.append(payload.get("macro", {}).get("text", ""))
    if payload["news"].get("fresh"):
        lines.append("\n**新闻焦点**")
        for a in payload["news"]["fresh"][:5]:
            lines.append(f"- [{a['source']}] {a['title']}")
    return "\n".join(lines)


def render_note(vault, payload: dict, narrative: str | None) -> str:
    fm = build_frontmatter({
        "类型": "每日简报",
        "日期": payload["date"],
        "持仓数": len(payload["positions"]),
        "引擎": "llm" if narrative else "local-fallback",
        "tags": ["听涛", "晨报"],
    })
    parts = [fm, f"# {payload['date']} 听涛晨报\n"]

    # 持仓表（确定性数据永远直接渲染，不经 LLM）
    rows = ["| 标的 | 现价 | 涨跌 | 信号分/立场 | 赛道 |",
            "| --- | --- | --- | --- | --- |"]
    for p in payload["positions"]:
        if p.get("signal_score") is not None:
            sig_txt = f"**{p['signal_score']}** {p.get('signal_stance') or ''}"
        elif (p.get("signal_stance") or "-") != "-":
            sig_txt = str(p["signal_stance"])
        else:
            sig_txt = "-"
        rows.append(
            f"| {p['name'] or ''} {p['symbol']} | {p['price'] or '—'} "
            f"| {_fmt_pct(p.get('change_pct'))} | {sig_txt} "
            f"| {'、'.join(p.get('tracks') or []) or '—'} |"
        )
    parts.append("## 持仓速览\n" + "\n".join(rows) + "\n")
    parts.append(f"## 宏观\n{payload['macro'].get('text', '')}\n")

    if payload["news"].get("fresh"):
        focus = "\n".join(
            f"- [[{a['title']}]]({a['link']}) — {a['source']}" if a.get('link')
            else f"- {a['title']} — {a['source']}"
            for a in payload["news"]["fresh"][:8]
        )
        parts.append("## 新闻焦点（24h）\n" + focus + "\n")
    if payload["news"].get("background"):
        bg = "\n".join(f"- {a['title']} — {a['source']}"
                       for a in payload["news"]["background"][:6])
        parts.append("## 背景（30d 内）\n" + bg + "\n")

    parts.append("## 今日解读\n")
    if narrative:
        parts.append(narrative.strip() + "\n")
    else:
        parts.append(fallback_narrative(payload) + "\n")

    note_rel = vault.briefing_relpath(payload["date"])
    vault.write_note(note_rel, "\n".join(parts), overwrite=True)
    return note_rel


def load_briefing(date_str: str | None = None) -> dict | None:
    d = date_str or today_str()
    p = Path(get_settings().briefings_dir) / f"{d}.json"
    if not p.is_file():
        return None
    from ..utils.common import read_json

    data = read_json(p)
    return {"date": d, **(data or {})}


def list_briefings(limit: int = 60) -> list[str]:
    bdir = Path(get_settings().briefings_dir)
    if not bdir.is_dir():
        return []
    files = sorted(bdir.glob("*.json"), reverse=True)[:limit]
    return [f.stem for f in files]


def _fmt_pct(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"
