"""PDF 文本抽取：逐页分块，识别章节结构，供 LLM 摘要与全文检索。"""

from __future__ import annotations

import fitz  # PyMuPDF

from ..utils.common import setup_logging

log = setup_logging("investlab.reports.text")

HEADING_RE = __import__("re").compile(
    r"^(?:[一二三四五六七八九十]+[、.．]|[0-9]{1,2}[、.．]|[0-9]{1,2}\.[0-9]|"
    r"(?:第[一二三四五六七八九十]+部分|附录|摘要|核心观点|投资要点|风险提示|目录))",
)


def extract_pdf(pdf_path) -> dict:
    """返回 {pages: [{page, text, blocks}], full_text, headings, n_pages}。"""
    doc = fitz.open(str(pdf_path))
    pages = []
    headings = []
    for i, page in enumerate(doc):
        try:
            text = page.get_text("text") or ""
        except Exception as exc:
            log.debug("第%d页文本抽取失败: %s", i + 1, exc)
            text = ""
        text = _normalize_ws(text)
        if text:
            pages.append({"page": i + 1, "text": text})
            for ln in text.splitlines():
                ln_s = ln.strip()
                if HEADING_RE.match(ln_s) and 2 <= len(ln_s) <= 40 and not ln_s.endswith(("。", "；")):
                    headings.append({"page": i + 1, "title": ln_s})
    doc.close()
    return {
        "n_pages": len(pages),
        "pages": pages,
        "full_text": "\n\n".join(p["text"] for p in pages),
        "headings": headings[:60],
    }


def section_chunks(extracted: dict, max_chars: int = 1800) -> list[dict]:
    """按标题切分 + 长度控制的大块（给 LLM 的上下文单元）。"""
    chunks: list[dict] = []
    cur_title = "开头"
    cur_buf: list[str] = []
    cur_len = 0

    def flush():
        nonlocal cur_len
        body = "\n".join(cur_buf).strip()
        if body:
            chunks.append({"section": cur_title, "text": body[:max_chars]})
        cur_buf.clear()
        cur_len = 0

    for page in extracted["pages"]:
        for ln in page["text"].splitlines():
            s = ln.strip()
            is_heading = bool(HEADING_RE.match(s)) and len(s) <= 40
            if is_heading:
                flush()
                cur_title = s[:40]
                continue
            cur_buf.append(s)
            cur_len += len(s)
            if cur_len >= max_chars:
                flush()
    flush()
    return chunks


# ------------------------------------------------------------------ 表格提取


def extract_tables(pdf_path, *, max_tables: int = 16,
                   min_rows: int = 2, min_cols: int = 2) -> list[dict]:
    """用 PyMuPDF 表格识别抽结构化表，返回 [{page, n_rows, n_cols, markdown, caption?}]。

    典型券商 PDF 的“竞品对比图 / 财务摘要”多为线框表格——这是它们的数据化通道。
    """

    out: list[dict] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.debug("打开 PDF 失败: %s", exc)
        return out
    try:
        for pno in range(len(doc)):
            if len(out) >= max_tables:
                break
            page = doc[pno]
            try:
                finder = page.find_tables()
                tabs = finder.tables
            except Exception as exc:
                log.debug("find_tables 失败 p%d: %s", pno + 1, exc)
                continue
            page_text = page.get_text("text") or ""
            cap_m = CAPTION_RE.search(page_text)
            for t in tabs[: max_tables - len(out)]:
                rows = t.extract()
                rows = [[_clean_cell(c) for c in r] for r in rows]
                rows = [r for r in rows if any(r)]
                if len(rows) < min_rows or len(rows[0]) < min_cols:
                    continue
                out.append({
                    "page": pno + 1,
                    "n_rows": len(rows),
                    "n_cols": len(rows[0]),
                    "caption": cap_m.group(0)[:60] if cap_m else "",
                    "markdown": _rows_to_md(rows),
                })
    finally:
        doc.close()
    return out


CAPTION_RE = __import__("re").compile(
    r"(?:表|Table)\s*[`´]?\s*(\d{1,3})\s*[:：.]?\s*([^\n]{2,50})"
)


def _clean_cell(c):
    if c is None:
        return ""
    return _re_ws.sub(" ", str(c)).strip()


_re_ws = __import__("re").compile(r"\s+")


def _rows_to_md(rows: list[list[str]], max_rows: int = 30) -> str:
    header = rows[0]
    lines = ["| " + " | ".join(h[:24] or "—" for h in header) + " |",
             "| " + " | ".join(["---"] * len(header)) + " |"]
    for r in rows[1:max_rows]:
        pad = r + [""] * (len(header) - len(r))
        lines.append("| " + " | ".join(c[:40] for c in pad[: len(header)]) + " |")
    if len(rows) > max_rows:
        lines.append(f"…（共 {len(rows)} 行）")
    return "\n".join(lines)


def _normalize_ws(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    out = []
    for ln in lines:
        if ln.strip() == "" and (not out or out[-1].strip() == ""):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def find_figures_context(extracted: dict, page_no: int, radius: int = 400) -> str:
    """取图表所在页附近的文本（作为图表理解 LLM 的上下文）。"""
    parts = []
    for p in extracted["pages"]:
        if abs(p["page"] - page_no) <= 0:
            t = p["text"]
            parts.append(t[:radius * 2])
    return "\n".join(parts)[: radius * 3]
