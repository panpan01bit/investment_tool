"""券商报告解析管线：合成 PDF 上的端到端断言。"""

from __future__ import annotations

import io

import pytest


def _make_sample_pdf() -> bytes:
    """构造含文本 + 位图 + 矢量柱状图的三页样例研报（内置中文字体）。"""
    import fitz

    CN = "china-s"  # PyMuPDF 内置简体字体名

    def txt(page, pos, s, size=11):
        page.insert_text(pos, s, fontsize=size, fontname=CN)

    doc = fitz.open()
    # ---- 第1页：标题/评级/目标价/日期/券商
    page = doc.new_page(width=595, height=842)
    txt(page, (72, 90), "中际旭创（300308.SZ）深度跟踪", 18)
    txt(page, (72, 120), "华泰证券研究所 | 2026年7月28日")
    txt(page, (72, 150), "投资评级：买入   目标价：220.00 元", 12)
    txt(page, (72, 180), "分析师：王小明 李研究")
    txt(page, (72, 220),
        "公司是全球光模块龙头，800G 出货占比提升，1.6T 进入放量周期。"
        "算力资本开支持续上行验证行业景气度。")

    # ---- 第2页：图表页
    p2 = doc.new_page()
    txt(p2, (72, 80), "图1：近四季营收与净利润增速对比", 12)
    x0, y0, w, h = 72, 100, 420, 260
    p2.draw_rect(fitz.Rect(x0, y0, x0 + w, y0 + h))
    for i, frac in enumerate([0.3, 0.55, 0.45, 0.75]):
        bx = x0 + 30 + i * 95
        bh = h * frac
        p2.draw_rect(fitz.Rect(bx, y0 + h - bh, bx + 40, y0 + h - 20),
                     fill=(0.2, 0.4, 0.8))
    p2.draw_line(fitz.Point(x0, y0 + h - 20), fitz.Point(x0 + w, y0 + h - 20))
    txt(p2, (72, 430), "资料来源：Wind，公司公告", 9)

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 240, 160))
    pix.clear_with(90)
    p2.insert_image(fitz.Rect(72, 470, 320, 650), stream=pix.tobytes("png"))
    txt(p2, (72, 665), "图2：产业链竞争格局示意", 12)

    # ---- 第3页：风险提示
    p3 = doc.new_page()
    txt(p3, (72, 80), "风险提示", 14)
    txt(p3, (72, 110), "下游资本开支不及预期；海外供应链政策变化。")

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ------------------------------------------------------------------ 元信息


def test_extract_meta_broker_rating_target():
    from investlab.reports.meta import extract_meta

    pdf = _make_sample_pdf()
    import fitz

    doc = fitz.open(stream=pdf, filetype="pdf")
    pages = [p.get_text("text") for p in doc]
    meta = extract_meta(pages, filename="300308_深度跟踪.pdf")
    doc.close()

    assert "华泰证券" in (meta["broker"] or "") or "华泰" in str(meta["broker"])
    assert meta["date"] == "2026-07-28"
    assert meta["rating"] == "买入"
    assert meta["target_price"] == 220.0
    assert any(s["symbol"].startswith("300308") for s in meta["symbols"])
    assert len(meta["analysts"]) >= 1


def test_detect_title():
    from investlab.reports.meta import detect_title

    head = "请务必阅读免责声明\n中际旭创（300308.SZ）深度跟踪\n华泰证券研究所"
    title = detect_title(head)
    assert "深度跟踪" in title


# ------------------------------------------------------------------ 文本/图表


def test_text_extraction_and_headings(tmp_path):
    from investlab.reports.text_extract import extract_pdf, section_chunks

    pdf_bytes = _make_sample_pdf()
    path = tmp_path / "sample.pdf"
    path.write_bytes(pdf_bytes)
    ex = extract_pdf(str(path))
    assert ex["n_pages"] == 3
    assert "光模块" in ex["full_text"]
    assert any(h["title"] == "风险提示" or "风险提示" in h["title"]
               for h in ex["headings"])
    chunks = section_chunks(ex)
    assert chunks and all(c["text"] for c in chunks)


def test_figure_extraction_finds_vector_chart_and_raster(tmp_path):
    from investlab.reports.figures import extract_figures

    pdf_bytes = _make_sample_pdf()
    path = tmp_path / "sample.pdf"
    path.write_bytes(pdf_bytes)
    figs = extract_figures(str(path))
    kinds = {f.kind for f in figs}
    assert "vector_chart" in kinds
    assert any(f.page == 2 for f in figs)
    # 位图区域也被捕获（raster 或被归入矢量合并区均可，但至少 2 个候选）
    assert len(figs) >= 2


# ------------------------------------------------------------------ 管线端到端（关闭视觉）


def test_ingest_and_parse_end_to_end(isolated_env):
    from investlab.obsidian.vault import new_vault
    from investlab.reports.pipeline import ingest_pdf_bytes, list_reports, parse_report

    new_vault().ensure_layout()
    pdf_bytes = _make_sample_pdf()
    rec = ingest_pdf_bytes(pdf_bytes, original_name="300308 深度跟踪 华泰证券.pdf")
    rid = rec["id"]
    assert rec["n_pages"] == 3
    assert rec["status"] == "text_ready"

    result = parse_report(rid, vision=False)
    record = result["record"]
    assert record["figures_meta"], "应抽到图表"
    assert record["figures_meta"][0]["file"].startswith("figures/")
    # LLM 未配置时摘要为 None 但笔记仍生成
    assert record["summary"] is None

    note_rel = result["obsidian_note"]
    v = new_vault()
    note_text = v.read_note(note_rel)
    assert note_text and "深度跟踪" in note_text
    assert "图表" in note_text
    # 索引表已维护
    index = v.read_note("30 报告库/Index.md")
    assert index and "| 日期 |" in index
    # 列表可见
    assert any(r["id"] == rid for r in list_reports())


def test_ingest_rejects_non_pdf(isolated_env):
    from investlab.reports.pipeline import ingest_pdf_bytes

    with pytest.raises(ValueError):
        ingest_pdf_bytes(b"not a pdf", original_name="x.pdf")


def test_reingest_returns_existing(isolated_env):
    from investlab.reports.pipeline import ingest_pdf_bytes

    data = _make_sample_pdf()
    r1 = ingest_pdf_bytes(data, original_name="a.pdf")
    r2 = ingest_pdf_bytes(data, original_name="b.pdf")
    assert r1["id"] == r2["id"]
    assert r2.get("already_ingested") is True
