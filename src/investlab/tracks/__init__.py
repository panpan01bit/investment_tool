"""赛道框架：加载 taxonomy.json，提供股票↔赛道映射与主线文案。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..datasources.symbols import normalize

_TAXONOMY_PATH = Path(__file__).parent / "taxonomy.json"
_cache: dict | None = None


def load_taxonomy(refresh: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is None or refresh:
        with open(_TAXONOMY_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def all_track_stocks() -> dict[str, list[str]]:
    """二级赛道 id → 内部规范代码列表（A股+港股+美股）。"""
    tax = load_taxonomy()
    out = {}
    for t in tax.get("secondary_tracks", []):
        codes = []
        for k in ("stocks_ch", "stocks_hk", "stocks_us"):
            for raw in t.get(k) or []:
                n = normalize(str(raw).split()[0])
                if n:
                    codes.append(n)
        out[t["id"]] = sorted(set(codes))
    return out


def tracks_for_symbol(symbol: str) -> list[dict]:
    """一个标的属于哪些赛道（含三级赛道引用），供持仓映射展示。"""
    s = normalize(symbol)
    base = str(s).split(".")[0].lstrip("0")
    hits = []
    tier3_parent_note = {}
    for t3 in load_taxonomy().get("tertiary_tracks", []):
        for entry in t3.get("stocks", []):
            code_raw = str(entry).split()[0]
            n = normalize(code_raw)
            if n == s or (n and base and str(n).split(".")[0].lstrip("0") == base):
                hits.append({"tier": 3, "id": t3["id"], "name": t3["name"],
                             "parent": t3["parent"], "entry": entry})
                tier3_parent_note[t3["parent"]] = True
    for tid, stocks in all_track_stocks().items():
        if s in stocks:
            if tid in tier3_parent_note:
                continue  # 已有更细的三级赛道映射
            meta = next(
                (t for t in load_taxonomy().get("secondary_tracks", []) if t["id"] == tid),
                {},
            )
            hits.append({
                "tier": 2, "id": tid,
                "name": meta.get("name", tid),
                "class": meta.get("class"),
                "maturity": meta.get("maturity"),
                "tam": meta.get("tam"),
            })
    return hits


def track_by_id(track_id: str) -> dict | None:
    tax = load_taxonomy()
    for t in tax.get("tertiary_tracks", []):
        if t["id"] == track_id:
            parent = next(
                (x for x in tax.get("secondary_tracks", []) if x["id"] == t.get("parent")),
                None,
            )
            return {"tier": 3, **t, "parent_meta": parent}
    for t in tax.get("secondary_tracks", []):
        if t["id"] == track_id:
            return {"tier": 2, **t}
    return None


def thesis_one_liner() -> str:
    tax = load_taxonomy()
    return f"{tax.get('thesis', '')} 配置框架：{json.dumps(tax.get('allocation_frame', {}), ensure_ascii=False)}"


def verification_checklist() -> list[str]:
    return list(load_taxonomy().get("verification_metrics", []))
