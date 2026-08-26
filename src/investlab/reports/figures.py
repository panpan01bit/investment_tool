"""图表提取：从报告 PDF 中挖出栅格图与矢量统计图区域，导出 PNG 附件。

两类来源：
1. 嵌入的位图（page.get_images）——截图/照片/logo；
2. 矢量绘图密集区域（page.get_drawings 聚类）——典型统计图/竞品对比图。
启发式过滤页眉 logo 与极小装饰块；每张图附带所属页码与附近文本上下文。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import fitz
from PIL import Image

from ..utils.common import setup_logging

log = setup_logging("investlab.reports.figures")

# 过滤阈值（PDF 点单位，1pt≈1/72英寸）
MIN_FIG_W = 110
MIN_FIG_H = 70
TOP_HEADER_BAND = 60          # 页顶页眉区
CAPTION_RE = re.compile(
    r"(?:图|表|Graph|Fig(?:ure)?)\s*[`´]?\s*(\d{1,3})\s*[:：.]?\s*([^\n]{2,50})"
)


@dataclass
class Figure:
    page: int
    bbox: tuple             # (x0,y0,x1,y1)
    kind: str               # raster | vector_chart
    png: bytes
    caption: str = ""
    context_text: str = ""  # 图表标题候选文本
    classify: dict = field(default_factory=dict)

    def to_meta(self) -> dict:
        return {
            "page": self.page,
            "bbox": list(self.bbox),
            "kind": self.kind,
            "caption": self.caption,
            "classify": self.classify,
        }


def extract_figures(pdf_path, *, max_figures: int = 40) -> list[Figure]:
    """抽取候选图表。结果按页排序。"""
    doc = fitz.open(str(pdf_path))
    figures: list[Figure] = []
    try:
        for pno in range(len(doc)):
            if len(figures) >= max_figures:
                break
            page = doc[pno]
            near_text = _nearby_caption(page.get_text("text") or "")

            for fig in _raster_figures(page, pno + 1):
                fig.context_text = near_text
                figures.append(fig)

            for region in _vector_chart_regions(page):
                pix = page.get_pixmap(clip=fitz.Rect(*region), dpi=150)
                fig = Figure(
                    page=pno + 1,
                    bbox=region,
                    kind="vector_chart",
                    png=pix.tobytes("png"),
                    caption=near_text,
                )
                figures.append(fig)
                if len(figures) >= max_figures:
                    break
    finally:
        doc.close()

    figures = _dedupe(figures)
    # 给矢量图按顺序补 caption 编号
    seq = {}
    for f in figures:
        seq[f.page] = seq.get(f.page, 0) + 1
        if not f.caption:
            f.caption = f"第{f.page}页·第{seq[f.page]}个图区"
    return figures


def _raster_figures(page: fitz.Page, page_no: int) -> list[Figure]:
    out: list[Figure] = []
    try:
        images = page.get_images(full=True)
    except Exception:
        return out
    for img in images:
        xref = img[0]
        try:
            rects = page.get_image_rects(xref)
            base = doc_pixmap_bytes(page.parent, xref)
            if not base:
                continue
            for r in rects or [fitz.Rect(0, 0, 0, 0)]:
                w, h = r.width, r.height
                if w < MIN_FIG_W or h < MIN_FIG_H:
                    continue                    # 太小：多半是 logo/icon
                if r.y1 < TOP_HEADER_BAND and h < MIN_FIG_H * 1.5:
                    continue                    # 页眉装饰
                with Image.open(io.BytesIO(base)) as im:
                    iw, ih = im.size
                    if iw < 90 or ih < 55:
                        continue                # 像素尺寸过小
                out.append(
                    Figure(page=page_no, bbox=(r.x0, r.y0, r.x1, r.y1),
                           kind="raster", png=base)
                )
                break  # 同一 xref 多位置只取首个大块
        except Exception as exc:
            log.debug("图像抽取失败 xref=%s: %s", xref, exc)
    return out


def doc_pixmap_bytes(doc: fitz.Document, xref: int) -> bytes | None:
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png")
    except Exception as exc:
        log.debug("xref 渲染失败 %s: %s", xref, exc)
        return None


def _vector_chart_regions(page: fitz.Page) -> list[tuple]:
    """把页面矢量绘图聚类成候选图表框。

    思路：收集 drawings 的矩形包围盒；做简单的网格密度聚类（一次合并相邻框）；
    面积过小 / 高度太扁（像分隔线）/ 覆盖整页（背景框）的丢弃。
    """
    boxes: list[fitz.Rect] = []
    try:
        for d in page.get_drawings():
            r = d.get("rect")
            if not r:
                continue
            if r.width < 3 or r.height < 3:
                continue
            boxes.append(fitz.Rect(r))
    except Exception as exc:
        log.debug("get_drawings 失败: %s", exc)
        return []

    if len(boxes) < 5:   # 少量线条不足以构成图表（边框+柱条也常只有几个元素）
        return []

    merged: list[fitz.Rect] = []
    used = [False] * len(boxes)
    boxes.sort(key=lambda b: (round(b.y0 / 24), b.x0))
    for i, b in enumerate(boxes):
        if used[i]:
            continue
        cur = fitz.Rect(b)
        changed = True
        while changed:
            changed = False
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                inflated = fitz.Rect(cur.x0 - 14, cur.y0 - 10, cur.x1 + 14, cur.y1 + 10)
                if inflated.intersects(boxes[j]):
                    cur |= boxes[j]
                    used[j] = True
                    changed = True
        used[i] = True
        w, h = cur.width, cur.height
        if w < MIN_FIG_W or h < MIN_FIG_H:
            continue
        if h < 26 and w > 200:
            continue                      # 分隔线
        page_area = abs(page.rect)
        if (w * h) > page_area * 0.92:
            continue                      # 整页背景框
        merged.append((cur.x0, cur.y0, cur.x1, cur.y1))

    # 合并互相重叠的大框
    merged.sort(key=lambda bb: (-((bb[2] - bb[0]) * (bb[3] - bb[1]))))
    kept: list[tuple] = []
    for bb in merged:
        r = fitz.Rect(bb)
        drop = False
        for kb in kept:
            kr = fitz.Rect(kb)
            if not r.intersects(kr):
                continue
            # 无交集时 PyMuPDF 的交集是“无限矩形”，改用容斥原理算重叠面积
            inter_area = r.get_area() + kr.get_area() - (fitz.Rect(r) | fitz.Rect(kr)).get_area()
            small_area = min(r.get_area(), kr.get_area())
            if small_area > 0 and inter_area / small_area > 0.7:
                drop = True
                break
        if not drop:
            kept.append(bb)
        if len(kept) >= 6:
            break
    return kept


def _nearby_caption(text: str) -> str:
    """找“图N：xxx / 表N xxx”标题行。"""
    m = CAPTION_RE.search(text or "")
    if m:
        return m.group(0)[:60]
    return ""


def _dedupe(figures: list[Figure]) -> list[Figure]:
    seen = set()
    out = []
    for f in figures:
        key = (f.page, round(f.bbox[0] / 20), round(f.bbox[1] / 20))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
