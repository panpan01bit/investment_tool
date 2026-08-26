"""图表视觉理解：把图片交给视觉模型做分类与数据结构化。

每张图输出：
  type: {statistical_chart, competitor_table, diagram, photo, logo, other}
  title, chart_kind(柱状/折线/饼/散点...), series: [{name, points:[{label, value}]}],
  takeaway(一句话), confidence(high/mid/low), needs_manual_check(bool)

纪律（对齐 UZI 原则）：模型读不出的字段保持 null，绝不编数字；
confidence=low 时整体标记“待人工核对”。
"""

from __future__ import annotations

import json

from ..llm.client import extract_json, get_llm
from ..utils.common import setup_logging

log = setup_logging("investlab.reports.vision")

VALID_TYPES = {"statistical_chart", "competitor_table", "diagram", "photo", "logo", "other"}
SKIP_TYPES = {"logo", "photo", "other"}

PROMPT = """你是券商研报图表分析助手。分析这张研报中的图片，返回严格的 JSON（不要多余文本）。
结合图中与上下文信息判断：
1) type：statistical_chart(统计图)|competitor_table(竞品对比表/图)|diagram(产业链/结构示意)|photo|logo|other
2) title：图表标题（若上下文提供则优先采用）
3) chart_kind：bar/line/pie/scatter/waterfall/table/diagram/none
4) axes：{x_label, y_label} 或 null
5) series：数组 [{name, unit, points:[{label, value}]}]。只转录图中**清晰可读**的数值；
   读不清的用 null 并减少该点；完全无法读取时 series=[]
6) takeaway：≤40字的核心信息
7) confidence：high|mid|low（low 表示图太模糊或行文与图不符）
8) source_note：图中标注的资料来源文字（若有）

规则：禁止编造图中不存在的数据。缺失字段一律 null。

上下文文本（可能含图表标题）：
<context>
{context}
</context>"""


def analyze_figure(png_bytes: bytes, context_text: str = "") -> dict:
    llm = get_llm()
    if llm is None:
        return {"type": "other", "error": "未配置 LLM，跳过视觉分析",
                "needs_manual_check": True}
    prompt = PROMPT.replace("{context}", (context_text or "")[:900])
    try:
        resp = llm.vision_image(prompt, image_b64=_b64(png_bytes),
                                temperature=0.1, max_tokens=1600)
    except Exception as exc:
        log.warning("视觉分析失败: %s", exc)
        return {"type": "other", "error": f"视觉分析失败: {exc}",
                "needs_manual_check": True}
    data = extract_json(resp.text)
    if not isinstance(data, dict):
        return {"type": "other", "error": "视觉模型未返回有效 JSON",
                "raw_head": resp.text[:300], "needs_manual_check": True}

    out = _normalize(data)
    if out.get("type") in SKIP_TYPES:
        out["skip"] = True
    out["needs_manual_check"] = out.get("confidence") == "low"
    return out


def batch_analyze(figures: list, *, progress=None) -> list[dict]:
    results = []
    for i, fig in enumerate(figures):
        res = analyze_figure(fig.png, fig.context_text or fig.caption)
        res["page"] = fig.page
        res["kind"] = fig.kind
        res["caption"] = fig.caption
        results.append(res)
        if progress:
            progress(i + 1, len(figures))
    return results


def _normalize(data: dict) -> dict:
    ftype = str(data.get("type") or "other")
    if ftype not in VALID_TYPES:
        ftype = "other"
    series = []
    for s in data.get("series") or []:
        if not isinstance(s, dict):
            continue
        pts = []
        for p in (s.get("points") or [])[:30]:
            if not isinstance(p, dict):
                continue
            v = p.get("value")
            pts.append({
                "label": str(p.get("label") or "")[:60] or None,
                "value": v if isinstance(v, (int, float)) else None,
            })
        series.append({
            "name": str(s.get("name") or "")[:60] or None,
            "unit": str(s.get("unit") or "")[:20] or None,
            "points": pts,
        })
    return {
        "type": ftype,
        "title": data.get("title") or None,
        "chart_kind": data.get("chart_kind") or None,
        "axes": data.get("axes") if isinstance(data.get("axes"), dict) else None,
        "series": series,
        "takeaway": (data.get("takeaway") or "")[:120] or None,
        "confidence": data.get("confidence") if data.get("confidence") in ("high", "mid", "low") else "low",
        "source_note": data.get("source_note") or None,
    }


def chart_data_to_markdown(classify: dict, fig_ref: str) -> str:
    """把 classify 结果渲染成 Obsidian 表格/要点。"""
    lines = []
    title = classify.get("title") or classify.get("caption") or "(未命名图表)"
    kind_cn = {
        "statistical_chart": "统计图", "competitor_table": "竞品对比",
        "diagram": "示意图", "photo": "照片", "logo": "标志", "other": "其他",
    }.get(classify.get("type"), classify.get("type"))
    flag = " ⚠️[待人工核对]" if classify.get("needs_manual_check") else ""
    lines.append(f"### {title}{flag}")
    meta_bits = [f"类型：{kind_cn}"]
    if classify.get("chart_kind"):
        meta_bits.append(f"形式：{classify['chart_kind']}")
    if classify.get("source_note"):
        meta_bits.append(f"来源标注：{classify['source_note']}")
    if classify.get("takeaway"):
        meta_bits.append(f"要点：{classify['takeaway']}")
    lines.append("- " + "；".join(meta_bits))
    for s in classify.get("series") or []:
        pts = [(p.get("label"), p.get("value")) for p in (s.get("points") or [])
               if p.get("value") is not None]
        if not pts:
            continue
        name = s.get("name") or "系列"
        unit = s.get("unit") or ""
        lines.append("")
        lines.append(f"| {name}（{unit}） | 数值 |")
        lines.append("| --- | --- |")
        for label, value in pts[:25]:
            lines.append(f"| {label} | {value} |")
    lines.append(f"\n{fig_ref}")
    return "\n".join(lines)


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


def parse_json_lenient(text: str):
    return extract_json(text)


def dumps_compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
