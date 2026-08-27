"""券商报告解析管线（编排层）。

流程：
  1) ingest：PDF 拷入库 data/reports/library/<sha>/，提取元信息与文本
  2) figures：位图 + 矢量图区域导出 PNG
  3) vision（可选）：视觉模型分类图表并结构化数值
  4) LLM 总结：核心观点 / 投资逻辑 / 风险
  5) 输出：Obsidian「30 报告库/<日期 券商 标题>」note.md + figures/ + report.json

所有步骤可独立重跑；视觉未配置时保留图片并标记待分析。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..config import get_settings
from ..llm.client import extract_json, get_llm
from ..obsidian.vault import Vault, build_frontmatter, new_vault
from ..utils.common import (
    atomic_write_bytes,
    safe_filename,
    setup_logging,
    today_str,
    write_json,
)
from . import figures as figmod
from . import text_extract as texmod
from . import vision as vismod
from .meta import extract_meta

log = setup_logging("investlab.reports")


class ReportRecord(dict):
    """dict 薄包装：带 id 属性便于 API 使用。"""


def ingest_pdf(pdf_path: str | Path, *, move: bool = False) -> dict:
    """把本地 PDF 收进库（CLI 用）。"""
    src = Path(pdf_path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"文件不存在: {src}")
    if src.suffix.lower() != ".pdf":
        raise ValueError("仅支持 PDF")
    rec = ingest_pdf_bytes(src.read_bytes(), original_name=src.name)
    if move:
        src.unlink(missing_ok=True)
    return rec


def ingest_pdf_bytes(data: bytes, *, original_name: str = "report.pdf") -> dict:
    """API 上传入口：内容哈希决定存储路径（不使用任何用户输入拼路径）。"""
    if not data[:5] == b"%PDF-":
        raise ValueError("不是有效的 PDF 文件")
    settings = get_settings()
    rid = hashlib.sha256(data).hexdigest()[:16]
    dest_dir = settings.reports_library_dir / rid
    dest_pdf = dest_dir / "report.pdf"
    if dest_dir.exists() and (dest_dir / "report.json").is_file():
        rec = read_record(rid) or {}
        rec["already_ingested"] = True
        return rec
    dest_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest_pdf, data)

    extracted = texmod.extract_pdf(dest_pdf)
    meta = extract_meta([p["text"] for p in extracted["pages"]],
                        filename=str(original_name)[:120])
    record = {
        "id": rid,
        "filename": str(original_name)[:120],
        "ingested_at": today_str(),
        "pdf_path": str(dest_pdf),
        "n_pages": extracted["n_pages"],
        "meta": meta,
        "status": "text_ready",
    }
    write_json(dest_dir / "report.json", record)
    return record


def read_record(rid: str) -> dict | None:
    p = get_settings().reports_library_dir / rid / "report.json"
    from ..utils.common import read_json

    return read_json(p)


def list_reports() -> list[dict]:
    lib = get_settings().reports_library_dir
    out = []
    if not lib.is_dir():
        return out
    for d in sorted(lib.iterdir(), reverse=True):
        rec_path = d / "report.json"
        if rec_path.is_file():
            from ..utils.common import read_json

            rec = read_json(rec_path)
            if rec:
                out.append({
                    "id": rec.get("id"),
                    "filename": rec.get("filename"),
                    "title": (rec.get("meta") or {}).get("title"),
                    "broker": (rec.get("meta") or {}).get("broker"),
                    "date": (rec.get("meta") or {}).get("date"),
                    "symbols": (rec.get("meta") or {}).get("symbols"),
                    "rating": (rec.get("meta") or {}).get("rating"),
                    "target_price": (rec.get("meta") or {}).get("target_price"),
                    "n_pages": rec.get("n_pages"),
                    "status": rec.get("status"),
                    "summary": ((rec.get("summary") or {}).get("one_liner")
                                if isinstance(rec.get("summary"), dict) else None),
                    "vision_done": bool(rec.get("figures_meta") or []),
                })
    return out


# ------------------------------------------------------------------ 完整解析


def parse_report(
    rid: str,
    *,
    vision: bool | None = None,
    max_figures: int = 24,
) -> dict:
    """对已 ingest 的报告做完整解析（图表 + 视觉 + LLM 摘要 + Obsidian 笔记）。"""
    settings = get_settings()
    use_vision = settings.report_vision_enabled if vision is None else vision
    rec = read_record(rid)
    if not rec:
        raise FileNotFoundError(f"报告不存在: {rid}")
    workdir = settings.reports_library_dir / rid
    pdf_path = Path(rec["pdf_path"])

    # ---- 文本（可选 MinerU 引擎，失败自动回退 PyMuPDF）
    extracted = texmod.extract_pdf(pdf_path)
    mineru_meta = None
    if getattr(settings, "report_engine", "pymupdf") == "mineru":
        try:
            from . import mineru_engine

            if mineru_engine.is_available():
                mineru_result = mineru_engine.convert(pdf_path, workdir)
                if mineru_result and mineru_result.get("sections"):
                    pages_by_no: dict[int, list[str]] = {}
                    for sec in mineru_result["sections"]:
                        pages_by_no.setdefault(sec["page"], []).append(
                            f"{sec['title']}\n{sec['text']}"
                        )
                    extracted = {
                        "n_pages": max(pages_by_no) if pages_by_no else extracted["n_pages"],
                        "pages": [
                            {"page": p, "text": "\n\n".join(t)}
                            for p, t in sorted(pages_by_no.items())
                        ],
                        "full_text": extracted["full_text"],
                        "headings": [
                            {"page": s["page"], "title": s["title"]}
                            for s in mineru_result["sections"]
                            if s["title"] != "正文"
                        ][:60],
                    }
                    mineru_meta = {
                        "tables": mineru_result.get("tables") or [],
                        "images": mineru_result.get("images") or [],
                    }
                    log.info("MinerU 引擎完成：sections=%d tables=%d images=%d",
                             len(mineru_result["sections"]),
                             len(mineru_meta["tables"]), len(mineru_meta["images"]))
            else:
                log.warning("INVESTLAB_REPORT_ENGINE=mineru 但 magic-pdf 未安装，回退 PyMuPDF")
        except Exception as exc:
            log.warning("MinerU 引擎异常，回退 PyMuPDF: %s", exc)

    # ---- 表格抽取（MinerU 表格优先；否则 PyMuPDF 线框表格识别）
    if mineru_meta and mineru_meta["tables"]:
        tables = [
            {"page": t["page"], "n_rows": 0, "n_cols": 0,
             "caption": t.get("caption") or "", "markdown": t["markdown"],
             "engine": "mineru"}
            for t in mineru_meta["tables"][:12]
        ]
    else:
        try:
            tables = texmod.extract_tables(pdf_path, max_tables=12)
        except Exception as exc:
            log.warning("表格抽取失败（跳过）: %s", exc)
            tables = []

    # ---- 图表抽取（MinerU 图片优先；否则矢量/位图区域检测）
    figs = []
    if mineru_meta and mineru_meta["images"]:
        for im in mineru_meta["images"]:
            p = workdir / im["file"]
            if p.is_file():
                figs.append(figmod.Figure(
                    page=im["page"], bbox=(0, 0, 0, 0), kind="raster",
                    png=p.read_bytes(), caption=im.get("caption") or "",
                ))
                if len(figs) >= max_figures:
                    break
    if not figs:
        figs = figmod.extract_figures(pdf_path, max_figures=max_figures)
    figures_rel: list[dict] = []
    for i, f in enumerate(figs, 1):
        rel = f"fig_{i:02d}_p{f.page}.png"
        (workdir / "figures").mkdir(exist_ok=True)
        (workdir / "figures" / rel).write_bytes(f.png)
        entry = {"file": f"figures/{rel}", **f.to_meta()}
        figures_rel.append(entry)

    # ---- 视觉理解（逐图）
    classify_results = []
    if use_vision and settings.llm_api_key and settings.llm_vision_model and figs:
        results = vismod.batch_analyze(figs[:max_figures])
        for res in results:
            classify_results.append(res)
    elif figs:
        classify_results = [
            {"type": "other", "caption": f.caption, "page": f.page,
             "skip_reason": "视觉模型未配置",
             "needs_manual_check": True}
            for f in figs
        ]

    # 把 classify 合入 figures_rel
    for k, cls in enumerate(classify_results):
        if k < len(figures_rel):
            figures_rel[k]["classify"] = {
                kk: vv for kk, vv in cls.items()
                if kk not in ("error", "raw_head") or vv
            }

    # ---- LLM 摘要
    summary = llm_summarize(extracted, rec.get("meta") or {})

    # ---- 更新 record
    real_classified = [
        c for c in classify_results
        if c.get("type") not in ("other", None) or c.get("series")
    ]
    for t in tables:
        if len(t.get("markdown") or "") > 4000:
            t["markdown"] = t["markdown"][:4000] + "\n…（截断）"
    rec.update({
        "status": "parsed" if (summary or not settings.llm_api_key) else "figures_ready",
        "engine": "mineru" if mineru_meta else "pymupdf",
        "headings": extracted.get("headings", []),
        "summary": summary,
        "tables_meta": tables,
        "figures_meta": figures_rel,
        "vision_used": bool(real_classified),
    })
    write_json(workdir / "report.json", rec)

    # ---- Obsidian 笔记
    vault_note = export_to_obsidian(rec, workdir)

    return {"ok": True, "record": _public_view(rec), "obsidian_note": vault_note}


def _public_view(rec: dict) -> dict:
    v = dict(rec)
    v.pop("pdf_path", None)  # 内部路径不外露
    return v


# ------------------------------------------------------------------ 摘要与输出


SUMMARY_PROMPT = """你是买方研究助理。基于以下券商报告文本，输出严格 JSON（不要多余文字）：
{{
 "one_liner": "一句话概括(≤60字)",
 "core_views": ["核心观点1", "..."],      // 最多5条，每条≤50字，必须来自原文
 "investment_logic": "投资逻辑简述(≤120字)",
 "risks": ["风险1","..."],               // 最多4条
 "catalysts": ["催化因素(若无则空数组)"],
 "rating_change": "评级或目标价变化；无则null"
}}
纪律：只使用原文信息；原文没有的写 null 或空数组，禁止编造。

报告标题：{title}
券商：{broker}

正文节选：
<report>
{text}
</report>"""


def llm_summarize(extracted: dict, meta: dict) -> dict | None:
    llm = get_llm()
    if llm is None:
        return None
    chunks = texmod.section_chunks(extracted)
    body = "\n\n".join(c["text"] for c in chunks)[:9000]
    if not body.strip():
        return None
    prompt = (
        SUMMARY_PROMPT.replace("{title}", meta.get("title") or "")
        .replace("{broker}", meta.get("broker") or "未知")
        .replace("{text}", body)
    )
    try:
        resp = llm.fast(prompt, temperature=0.2, response_json=True, max_tokens=1800)
    except Exception as exc:
        log.warning("摘要生成失败: %s", exc)
        return None
    data = extract_json(resp.text)
    if not isinstance(data, dict):
        log.warning("摘要 JSON 解析失败: %s", resp.text[:200])
        return None
    for key in ("core_views", "risks", "catalysts"):
        v = data.get(key)
        data[key] = [str(x)[:80] for x in v][:6] if isinstance(v, list) else []
    return data


def export_to_obsidian(rec: dict, workdir: Path) -> str:
    """生成 Obsidian 报告笔记。"""
    vault: Vault = new_vault()
    meta = rec.get("meta") or {}
    date_str = meta.get("date") or rec.get("ingested_at") or today_str()
    broker = meta.get("broker") or "未知机构"
    title = meta.get("title") or Path(rec.get("filename") or rec["id"]).stem
    folder = vault.report_dir(date_str, broker, title)

    fm = build_frontmatter({
        "类型": "券商报告",
        "日期": date_str,
        "券商": broker,
        "评级": meta.get("rating") or "",
        "目标价": meta.get("target_price"),
        "标的": [s.get("symbol") for s in (meta.get("symbols") or [])],
        "报告ID": rec["id"],
        "页数": rec.get("n_pages"),
        "tags": ["研报", broker],
    })

    parts = [fm, f"# {title}\n"]
    summ = rec.get("summary") or {}
    if summ.get("one_liner"):
        parts.append(f"> **一句话**：{summ['one_liner']}\n")
    if summ.get("core_views"):
        parts.append("## 核心观点\n" + "\n".join(f"- {v}" for v in summ["core_views"]) + "\n")
    if summ.get("investment_logic"):
        parts.append(f"## 投资逻辑\n{summ['investment_logic']}\n")
    if summ.get("catalysts"):
        parts.append("## 催化\n" + "\n".join(f"- {v}" for v in summ["catalysts"]) + "\n")
    if summ.get("risks"):
        parts.append("## 风险\n" + "\n".join(f"- {v}" for v in summ["risks"]) + "\n")

    # 关联个股研究链接
    symbols = [s.get("symbol") for s in (meta.get("symbols") or []) if s.get("symbol")]
    if symbols:
        links = " · ".join(f"[[{safe_filename(s)}]]" for s in symbols)
        parts.append(f"**涉及标的**：{links}\n")

    # 图表区
    figs_meta = rec.get("figures_meta") or []
    if figs_meta:
        parts.append(f"## 图表（{len(figs_meta)}张）\n")
        parts.append("> 注：图片在笔记同目录的 figures 文件夹中。\n")
        for _i, fr in enumerate(figs_meta, 1):
            cls = fr.get("classify") or {}
            fname = Path(fr["file"]).name
            ref = f"![](figures/{fname})"
            block = vismod.chart_data_to_markdown({**cls, "caption": fr.get("caption")}, ref)
            parts.append(block + "\n")

    # 数据表格区（竞品对比/财务摘要的结构化结果）
    tables_meta = rec.get("tables_meta") or []
    if tables_meta:
        parts.append(f"## 数据表格（识别到 {len(tables_meta)} 张）\n")
        for t in tables_meta[:6]:
            title_bits = [f"第{t['page']}页", f"{t['n_rows']}行×{t['n_cols']}列"]
            if t.get("caption"):
                title_bits.insert(0, t["caption"])
            flag = "" if t.get("markdown") else " ⚠️[空表待核对]"
            parts.append(f"### {' · '.join(title_bits)}{flag}\n")
            parts.append(t["markdown"] + "\n")
        if len(tables_meta) > 6:
            rest = sum(t.get("n_rows", 0) for t in tables_meta[6:])
            parts.append(
                f"> 另有 {len(tables_meta) - 6} 张表格（约 {rest} 行）见 report.json 的 tables_meta。\n"
            )

    parts.append("---\n"
                 f"*解析时间：{today_str()} · 由 investlab 本地解析（engine v2）*")

    content = "\n".join(parts)
    note_rel = f"{folder}/{safe_filename(title)}.md"
    # 图片复制到 Obsidian，附件跟随笔记（引用统一为相对 figures/ 路径）
    fig_dir_abs = vault.abs_path(f"{folder}/figures")
    fig_dir_abs.mkdir(parents=True, exist_ok=True)
    for fr in figs_meta:
        src = workdir / fr["file"]
        if src.is_file():
            shutil.copy2(src, fig_dir_abs / src.name)

    vault.write_note(note_rel, content, overwrite=True)
    update_report_index(vault, {
        "note": note_rel, "date": date_str, "broker": broker,
        "title": title, "symbols": symbols,
    })
    return note_rel


def update_report_index(vault: Vault, entry: dict) -> None:
    """维护 30 报告库/Index.md 表格目录（Dataview 友好）。"""
    index_rel = "30 报告库/Index.md"
    existing = vault.read_note(index_rel) or ""
    if entry["title"][:20] in existing:
        return
    header = (
        "# 报告库索引\n\n> Dataview 友好目录；由 investlab 自动维护。\n\n"
        "| 日期 | 机构 | 报告 |\n| --- | --- | --- |\n"
    )
    note_link = Path(entry["note"]).with_suffix("").as_posix()
    row = f"| {entry['date']} | {entry['broker']} | [[{note_link}\\|{entry['title']}]] |\n"
    if existing.startswith("# 报告库索引"):
        vault.write_note(
            index_rel, existing.rstrip("\n") + "\n" + row, overwrite=True
        )
    else:
        vault.write_note(index_rel, header + row, overwrite=True)
