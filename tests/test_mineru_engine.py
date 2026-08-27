"""MinerU content_list 解析（纯逻辑层，不依赖 magic-pdf 安装）。"""

from __future__ import annotations

import json
from pathlib import Path

from investlab.reports import mineru_engine

SAMPLE = [
    {"type": "text", "text": "一、核心观点", "page_idx": 0},
    {"type": "text", "text": "光模块景气度持续上行，800G 出货占比提升。", "page_idx": 0},
    {"type": "text", "text": "二、财务分析", "page_idx": 1},
    {"type": "table",
     "table_body": "<table><tr><th>公司</th><th>增速</th></tr>"
                   "<tr><td>中际旭创</td><td>35%</td></tr></table>",
     "table_caption": "表1 竞品对比", "page_idx": 1},
    {"type": "image", "img_path": "images/fig1.jpg", "image_caption": "图1 产业链", "page_idx": 2},
    {"type": "text", "text": "风险提示：下游资本开支不及预期。", "page_idx": 2},
]


def _write_cl(tmp_path: Path, blocks) -> Path:
    p = tmp_path / "sample_content_list.json"
    p.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    return p


def test_parse_content_list_sections_and_tables(tmp_path):
    parsed = mineru_engine.parse_content_list(_write_cl(tmp_path, SAMPLE))
    # 标题块开启新 section；无正文的标题节不产出（“二、财务分析”节只有表格）
    assert len(parsed["sections"]) == 1
    assert parsed["sections"][0]["title"] == "一、核心观点"
    assert "800G" in parsed["sections"][0]["text"]
    assert parsed["tables"][0]["page"] == 2
    assert "中际旭创" in parsed["tables"][0]["markdown"]
    assert parsed["images"][0]["file"] == "images/fig1.jpg"


def test_html_table_to_markdown():
    md = mineru_engine.html_table_to_markdown(
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    )
    lines = md.splitlines()
    assert lines[0].startswith("| A | B |")
    assert "| --- | --- |" in lines[1]
    assert "| 1 | 2 |" in lines[2]


def test_html_table_empty():
    assert mineru_engine.html_table_to_markdown("") == ""
    assert mineru_engine.html_table_to_markdown("<p>no table</p>") == ""


def test_long_section_chunking(tmp_path):
    blocks = [
        {"type": "text", "text": "长文本段落 " * 700, "page_idx": 0},   # ~4200字
        {"type": "text", "text": "长文本段落 " * 700, "page_idx": 0},
    ]
    parsed = mineru_engine.parse_content_list(_write_cl(tmp_path, blocks))
    assert len(parsed["sections"]) >= 2  # 超3500字自动切块


def test_is_available_returns_bool():
    assert isinstance(mineru_engine.is_available(), bool)


def test_malformed_content_list(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"not": "a list"}', encoding="utf-8")
    parsed = mineru_engine.parse_content_list(p)
    assert parsed == {"sections": [], "tables": [], "images": []}
