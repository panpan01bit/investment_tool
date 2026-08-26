"""Obsidian Vault 写入、报告解析（用真实PDF结构）与赛道框架。"""

from __future__ import annotations

import re

from investlab.obsidian.vault import build_frontmatter, new_vault
from investlab.tracks import (
    all_track_stocks,
    load_taxonomy,
    thesis_one_liner,
    tracks_for_symbol,
)
from investlab.utils.common import safe_filename


def test_safe_filename():
    assert safe_filename('a/b:c*d?"<>|#^[]') == "a b c d"
    assert safe_filename("") == "untitled"
    assert len(safe_filename("x" * 200, max_len=80)) <= 80


def test_frontmatter_lists_and_quotes():
    fm = build_frontmatter({"a": 1, "b": ["x", "y"], "c": "含: 冒号", "d": True})
    assert fm.startswith("---\n") and fm.endswith("\n---")
    assert "- x" in fm and '"含: 冒号"' in fm


def test_vault_write_read_and_no_overwrite(isolated_env):
    v = new_vault()
    p1 = v.write_note("10 听涛日报/n1.md", "hello")
    p2 = v.write_note("10 听涛日报/n1.md", "world")   # 不覆盖 → 新文件
    assert p1 != p2
    assert v.read_note("10 听涛日报/n1.md") == "hello"
    assert "n1 2" in p2.name


def test_vault_path_escape_blocked(isolated_env):
    v = new_vault()
    try:
        v.abs_path("../../outside.md")
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_attachment_bytes(isolated_env):
    v = new_vault()
    p = v.write_attachment("30 报告库/x/figures/fig.png", b"\x89PNG fake")
    assert p.read_bytes().startswith(b"\x89PNG")


# ------------------------------------------------------------------ 赛道框架


def test_taxonomy_shape():
    tax = load_taxonomy()
    assert len(tax["secondary_tracks"]) >= 18
    assert len(tax["tertiary_tracks"]) == 8
    ids = {t["id"] for t in tax["secondary_tracks"]}
    for t3 in tax["tertiary_tracks"]:
        parent = t3.get("parent")
        if parent:
            assert parent in ids, f"三级赛道父级缺失: {t3['id']}->{parent}"


def test_track_stock_codes_normalized():
    stocks = all_track_stocks()
    sym_re = re.compile(r"^\d{6}\.(SS|SZ)$|^\d{5}\.HK$|^[A-Z][A-Z0-9.\-]{0,9}$")
    total = 0
    for tid, syms in stocks.items():
        for s in syms:
            assert sym_re.match(s), f"{tid} 非法代码 {s}"
            total += 1
    assert total > 60  # 框架内代表标的覆盖度


def test_tracks_for_symbol_optical():
    hits = tracks_for_symbol("300308.SZ")
    names = [h["name"] for h in hits]
    assert any("光模块" in n or "CPO" in n for n in names)
    # 应包含三级赛道 1.6T 光模块
    assert any(h.get("tier") == 3 for h in hits)


def test_thesis_line_mentions_two_lines():
    line = thesis_one_liner()
    assert "A类" in line and "B类" in line
