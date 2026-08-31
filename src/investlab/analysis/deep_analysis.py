"""个股深度分析：确定性取数 → LLM 结构化研判 → Obsidian 研究笔记。

阶段一（Python 计算，绝不编造）：
  行情/K线/技术信号/基本面快照/赛道映射/相关研报清单 + 联网搜索（可选）
阶段二（LLM）：
  多空辩论式结论，必须引用阶段一数字；输出 JSON 受 schema 约束；
  数据缺口显式列出（“--”展示），禁止用默认值假装成功。
"""

from __future__ import annotations

import json

from ..config import get_settings
from ..datasources.candles import get_candles
from ..datasources.fundamentals import get_fundamentals
from ..datasources.quotes import get_quote
from ..llm.client import extract_json, get_llm
from ..obsidian.vault import build_frontmatter, new_vault
from ..quant.signals import compute_signals
from ..tracks import tracks_for_symbol
from ..utils.common import safe_filename, setup_logging, today_str

log = setup_logging("investlab.analysis")


def gather_facts(symbol: str, *, use_search: bool = True) -> dict:
    """阶段一：所有可验证事实。"""
    s = symbol
    facts: dict = {"symbol": s, "as_of": today_str()}

    quote = get_quote(s)
    facts["quote"] = {k: quote.get(k) for k in ("name", "price", "prev_close",
                                                "change_pct", "currency", "source")}

    candles = get_candles(s, days=280)
    signals = compute_signals(s)
    facts["technical"] = {
        "score": signals.score,
        "stance": signals.stance,
        "rules": [r.__dict__ for r in signals.rules if r.direction != 0],
        "indicators": signals.indicators,
        "gaps": signals.gaps,
        "candles_used": len(candles),
    }

    facts["fundamentals"] = get_fundamentals(s)
    facts["tracks"] = tracks_for_symbol(s)
    facts["related_reports"] = _related_reports(s)

    # 联网增强项（搜索 + 社媒热度）相互独立，并行执行避免超时叠加；
    # 各自失败静默降级，只记录缺口不阻塞分析。
    def _web() -> None:
        try:
            from ..search import format_hits_context, search

            name = (facts["quote"] or {}).get("name") or s
            hits = search(f"{name} {s} 最新业绩 指引", max_results=6)
            facts["web_context"] = format_hits_context(hits, limit_chars=1600)
            facts["web_hits"] = [
                {"title": h["title"], "url": h["url"]} for h in hits[:6]
            ]
        except Exception as exc:
            log.debug("搜索失败（跳过 web_context）: %s", exc)
            facts["web_context"] = ""

    def _social() -> None:
        # Reddit/HN/Polymarket/StockTwits/GitHub 免费源，30 分钟缓存；
        # 全部失败时 items 为空并如实记录 source_status
        try:
            from ..datasources.social import pulse_for_symbol

            facts["social"] = pulse_for_symbol(s)
        except Exception as exc:
            log.debug("社媒热度失败（跳过）: %s", exc)

    if use_search:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(fn) for fn in (_web, _social)]
            for f in futures:
                f.result()
    else:
        _social()
    return facts


def analyze_symbol(symbol: str, *, use_search: bool = True) -> dict:
    """完整深度分析。返回 JSON 结果并写 Obsidian 笔记。"""
    s = symbol
    facts = gather_facts(s, use_search=use_search)
    verdict = llm_verdict(facts)
    result = {
        "symbol": s,
        "facts": facts,
        "verdict": verdict,          # 可能为 None（未配置 LLM）
        "ts": today_str("%Y-%m-%d %H:%M"),
    }
    note_rel = export_research_note(result)
    result["obsidian_note"] = note_rel

    # 缓存最近一次分析供 API 使用
    from ..utils.common import write_json

    write_json(get_settings().data_dir / "research" / f"{safe_filename(s)}.json", result)
    return result


# ------------------------------------------------------------------ LLM 阶段


VERDICT_PROMPT = """你是资深买方分析师。基于以下**已验证数据**对该标的做研判，输出严格 JSON：

{{
 "headline": "一句话结论(≤50字)",
 "bull_case": ["多头论据(须引用数据)", ...],   // ≤4条
 "bear_case": ["空头论据/风险(须引用数据)", ...], // ≤4条
 "position_advice": {{
   "stance": "增持|持有|观望|回避",
   "entry_zone": "参考买入区间描述或null",
   "invalidation": "什么信号出现说明逻辑破坏(引用具体指标)"
 }},
 "thesis_fit": {{                      // 与《AI生产端》主线契合度
   "fits": true/false,
   "comment": "该标的属于哪条链(A类capex/B类生产率)、处于兑现还是主题阶段"
 }},
 "confidence": "high|mid|low"
}}

纪律：
- 只能引用 <facts> 中出现的数字；缺口用 "--" 表示并在 gap_notes 列出；
- 技术分 score 为负时 bear_case 必须出现对应理由；
- 禁止"基本面良好""前景广阔"这类无数字的套话。

<facts>
{json}
</facts>"""


def llm_verdict(facts: dict) -> dict | None:
    llm = get_llm()
    if llm is None:
        return None
    prompt = VERDICT_PROMPT.replace("{json}", json.dumps(facts, ensure_ascii=False, default=str))
    try:
        resp = llm.think(prompt, temperature=0.35, max_tokens=2200, response_json=True)
    except Exception as exc:
        log.warning("研判生成失败: %s", exc)
        return None
    data = extract_json(resp.text)
    if not isinstance(data, dict):
        log.warning("研判 JSON 解析失败: %s", resp.text[:200])
        return None
    for key in ("bull_case", "bear_case"):
        data[key] = [str(x)[:100] for x in (data.get(key) or [])][:5]
    return data


# ------------------------------------------------------------------ 输出


def export_research_note(result: dict) -> str:
    vault = new_vault()
    s = result["symbol"]
    name = ((result.get("facts") or {}).get("quote") or {}).get("name") or ""
    facts = result.get("facts") or {}
    v = result.get("verdict") or {}
    tech = (facts.get("technical") or {})
    ind = tech.get("indicators") or {}

    fm = build_frontmatter({
        "类型": "个股研究",
        "代码": s,
        "名称": name,
        "技术分": tech.get("score"),
        "更新时间": result.get("ts"),
        "tags": ["研究", s],
    })
    parts = [fm, f"# {name} {s} · 深度分析\n"]

    if v.get("headline"):
        conf = v.get("confidence") or "mid"
        parts.append(f"> **结论**：{v['headline']}（置信度 {conf}）\n")

    # 事实表
    q = facts.get("quote") or {}
    rows = ["| 项目 | 数值 |", "| --- | --- |"]
    price = q.get("price")
    rows.append(f"| 现价 | {price if price is not None else '--'} {q.get('currency') or ''} |")
    rows.append(f"| 日涨跌 | {_pct(q.get('change_pct'))} |")
    for k, label in (("rsi14", "RSI14"), ("ma20", "MA20"), ("ma200", "MA200"),
                     ("ann_vol", "年化波动"), ("max_dd_1y", "1年最大回撤")):
        if ind.get(k) is not None:
            unit = "%" if k in ("ann_vol", "max_dd_1y") else ""
            rows.append(f"| {label} | {ind[k]}{unit} |")
    fund = facts.get("fundamentals") or {}
    for k, label in (("pe", "PE(TTM)"), ("pb", "PB"), ("roe", "ROE"),
                     ("revenue_yoy", "营收同比%"), ("profit_yoy", "利润同比%"),
                     ("market_cap_yi", "市值(亿)")):
        if fund.get(k) is not None:
            rows.append(f"| {label} | {fund[k]} |")
    parts.append("## 关键数据\n" + "\n".join(rows) + "\n")

    # 技术 rules
    rules = tech.get("rules") or []
    if rules:
        lines = ["## 技术信号\n"]
        for r in rules:
            icon = "🟢" if r.get("direction") > 0 else "🔴"
            lines.append(f"- {icon} **{r['name']}**（权重{r.get('weight')}）：{r.get('reason')}")
        parts.append("\n".join(lines) + "\n")
    gaps = tech.get("gaps") or []
    if gaps:
        parts.append("> ⚠️ 数据缺口：" + "；".join(gaps) + "\n")

    # 赛道映射
    tr = facts.get("tracks") or []
    if tr:
        tlines = [f"- {'三级' if t.get('tier') == 3 else '二级'}赛道：{t['name']}"
                  + (f"（成熟度：{t.get('maturity')}）" if t.get("maturity") else "")
                  for t in tr]
        parts.append("## 赛道定位\n" + "\n".join(tlines) + "\n")

    if v:
        b = "\n".join(f"- {x}" for x in v.get("bull_case") or [])
        r_ = "\n".join(f"- {x}" for x in v.get("bear_case") or [])
        parts.append("## 多头逻辑\n" + (b or "- --"))
        parts.append("## 空头与风险\n" + (r_ or "- --"))
        pa = v.get("position_advice") or {}
        bits = [f"立场：**{pa.get('stance') or '--'}**"]
        if pa.get("entry_zone"):
            bits.append(f"参考区间：{pa['entry_zone']}")
        if pa.get("invalidation"):
            bits.append(f"证伪条件：{pa['invalidation']}")
        fit = v.get("thesis_fit") or {}
        if fit.get("comment"):
            bits.append(f"主线契合：{fit['comment']}")
        parts.append("## 操作建议\n- " + "\n- ".join(bits) + "\n")

    web = facts.get("web_hits") or []
    if web:
        wl = "\n".join(f"- [{h['title']}]({h['url']})" for h in web[:5])
        parts.append("## 网络参考[web]\n" + wl + "\n")

    rel_reports = facts.get("related_reports") or []
    if rel_reports:
        rl = "\n".join(f"- [[{r}]]" for r in rel_reports)
        parts.append("## 相关研报\n" + rl + "\n")

    parts.append(f"\n---\n*investlab 本地分析 {result.get('ts')}；"
                 "本笔记为研究框架输出，非投资建议。*")

    note_rel = vault.research_relpath(s, name)
    path = vault.write_note(note_rel, "\n".join(parts), overwrite=True)
    return str(path.relative_to(vault.path))


def _related_reports(symbol: str) -> list[str]:
    """报告库中涉及该标的的报告 Obsidian 链接名。"""
    from ..reports.pipeline import list_reports

    out = []
    base = symbol.split(".")[0].lstrip("0")
    for rec in list_reports():
        syms = rec.get("symbols") or []
        hit = False
        if isinstance(syms, list) and syms and isinstance(syms[0], dict):
            hit = any(
                str(x.get("symbol") or "").split(".")[0].lstrip("0").upper()
                == base.upper()
                for x in syms
            )
        title_bits = f"{rec.get('date', '')} {rec.get('broker', '')} {rec.get('title', '')}".strip()
        if hit or (base and base in title_bits):
            out.append(title_bits)
    return out[:8]


def normalize_eq(a: str, base: str) -> bool:
    return a.split(".")[0].lstrip("0").upper() == base.upper()


def _pct(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"
