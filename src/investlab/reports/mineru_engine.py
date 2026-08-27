"""MinerU 研报解析引擎（可选，实验性，默认关闭）。

MinerU（上海 AI Lab，opendatalab/MinerU）是当前中文研报 PDF→Markdown/JSON 的
最强开源方案，复杂表格与公式明显优于裸 PyMuPDF。

接入方式（无子进程，直接调用其 Python API；未安装时自动回退 PyMuPDF 管线）：
  1) 安装：pip install "magic-pdf[full]"（需下载模型权重）
  2) .env 开启：INVESTLAB_REPORT_ENGINE=mineru

安全说明：本模块不做任何 shell/子进程调用；magic-pdf 仅经 importlib 惰性加载，
其模型推理在进程内完成。magic-pdf 各版本 Python API 变动较大，这里按官方
文档做了多版本兼容尝试，全部失败时返回 None 并提示回退。

输出约定（auto 模式 content_list.json）：
  block 列表：{type: text|table|image, text?/table_body?(html)/img_path?, page_idx}
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
from pathlib import Path

from ..utils.common import setup_logging

log = setup_logging("investlab.reports.mineru")

_RE_HEADING = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.．]|[0-9]{1,2}[、.．]\s*\S|"
    r"第[一二三四五六七八九十]+部分|摘要|核心观点|投资要点|风险提示)"
)


def is_available() -> bool:
    """magic-pdf 包是否可导入（含其核心依赖）。"""
    try:
        importlib.import_module("magic_pdf.data.dataset")
        return True
    except Exception:
        return False


def _pdf_bytes(pdf_path: Path) -> bytes:
    return Path(pdf_path).read_bytes()


def run_mineru(pdf_path: Path, out_dir: Path) -> Path | None:
    """进程内调用 MinerU，产出 content_list.json 到 out_dir，返回其路径。

    兼容两种主流 API 形态（magic-pdf 1.x/2.x 均有报告）：
      A) PymuDocDataset + apply() 全自动管道
      B) FileBasedDataWriter 伴随输出（content_list 由管道落盘）
    任一环节失败返回 None（调用方回退 PyMuPDF）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_writer = None
    img_writer = None
    try:
        from magic_pdf.data.data_reader_writer import FileBasedDataWriter
        from magic_pdf.data.dataset import PymuDocDataset

        md_writer = FileBasedDataWriter(str(out_dir))
        img_writer = FileBasedDataWriter(str(out_dir / "images"))

        ds = PymuDocDataset(_pdf_bytes(Path(pdf_path)))
        # 形态 A：多版本 apply 兼容（无参/带pipe返回管道结果）
        pipe = None
        try:
            pipe = ds.apply()  # type: ignore[call-arg]
        except TypeError:
            try:
                pipe = ds.apply(None, None)  # type: ignore[call-arg]
            except Exception:
                pipe = None
        if pipe is None:
            log.warning("MinerU Python API 调用失败（版本不兼容），回退 PyMuPDF")
            return None

        # 管道结果落盘：md 与 content_list
        name = Path(pdf_path).stem
        try:
            pipe.dump_md(md_writer, f"{name}.md", img_writer)  # type: ignore[attr-defined]
        except Exception as exc:
            log.debug("dump_md 失败（不影响 content_list）: %s", exc)
        try:
            pipe.dump_content_list(md_writer, f"{name}_content_list.json", img_writer)  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("content_list 落盘失败: %s", exc)
            return None
    except ImportError as exc:
        log.warning("magic-pdf 导入失败: %s，回退 PyMuPDF", exc)
        return None
    except Exception as exc:
        log.warning("MinerU 推理失败: %s，回退 PyMuPDF", exc)
        return None

    candidates = sorted(out_dir.rglob("*_content_list.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def parse_content_list(cl_path: Path) -> dict:
    """content_list.json → {sections, tables, images}（investlab 内部统一结构）。"""
    blocks = json.loads(Path(cl_path).read_text(encoding="utf-8"))
    sections: list[dict] = []
    tables: list[dict] = []
    images: list[dict] = []
    cur_title = "正文"
    cur_buf: list[str] = []

    def flush(page: int):
        body = "\n".join(cur_buf).strip()
        if body:
            sections.append({"page": page, "title": cur_title[:40], "text": body[:4000]})
        cur_buf.clear()

    last_page = 1
    for b in blocks if isinstance(blocks, list) else []:
        btype = str(b.get("type") or "")
        page = int(b.get("page_idx") or 0) + 1
        last_page = page
        if btype == "text":
            text = str(b.get("text") or "").strip()
            if not text:
                continue
            if _RE_HEADING.match(text) and 2 <= len(text) <= 40:
                flush(page)
                cur_title = text
                continue
            cur_buf.append(text)
            if sum(len(x) for x in cur_buf) > 3500:
                flush(page)
        elif btype == "table":
            md = html_table_to_markdown(str(b.get("table_body") or ""))
            caption = str(b.get("table_caption") or "")
            if md:
                tables.append({"page": page, "caption": caption[:60], "markdown": md})
        elif btype == "image":
            img = str(b.get("img_path") or "")
            cap = str(b.get("image_caption") or "")
            if img:
                images.append({"page": page, "file": img, "caption": cap[:60]})
    flush(last_page)
    return {"sections": sections, "tables": tables, "images": images}


def html_table_to_markdown(html: str) -> str:
    """MinerU table_body 是简单 <table> HTML；用标准库解析为 Markdown。"""
    if not html.strip():
        return ""
    try:
        from html.parser import HTMLParser

        class _P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows: list[list[str]] = []
                self._row: list[str] | None = None
                self._cell: list[str] | None = None

            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self._row = []
                elif tag in ("td", "th"):
                    self._cell = []

            def handle_endtag(self, tag):
                if tag in ("td", "th") and self._cell is not None and self._row is not None:
                    self._row.append(" ".join("".join(self._cell).split()))
                    self._cell = None
                elif tag == "tr" and self._row is not None:
                    if any(self._row):
                        self.rows.append(self._row)
                    self._row = None

            def handle_data(self, data):
                if self._cell is not None:
                    self._cell.append(data)

        p = _P()
        p.feed(html)
        if not p.rows:
            return ""
        ncols = max(len(r) for r in p.rows)
        lines = [
            "| " + " | ".join((r + [""] * ncols)[:ncols]) + " |" for r in p.rows[:40]
        ]
        lines.insert(1, "| " + " | ".join(["---"] * ncols) + " |")
        if len(p.rows) > 40:
            lines.append(f"…（共 {len(p.rows)} 行）")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("表格HTML解析失败: %s", exc)
        return ""


def convert(pdf_path: Path, workdir: Path) -> dict | None:
    """对外主入口：pdf → 解析结果（图片复制到 workdir/figures）。"""
    out_dir = Path(workdir) / "mineru_out"
    cl = run_mineru(Path(pdf_path), out_dir)
    if not cl:
        return None
    result = parse_content_list(cl)
    figures_dir = Path(workdir) / "figures"
    figures_dir.mkdir(exist_ok=True)
    base = Path(cl).parent
    for im in result.get("images", []):
        src = base / im["file"]
        if src.is_file():
            dest = figures_dir / Path(im["file"]).name
            shutil.copy2(src, dest)
            im["file"] = f"figures/{dest.name}"
    shutil.rmtree(out_dir, ignore_errors=True)
    return result
