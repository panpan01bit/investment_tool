"""赛道筛选器：对赛道代表标的批量跑信号/行情，输出按分排序的观察矩阵。"""

from __future__ import annotations

from ..datasources.quotes import get_quotes
from ..tracks import all_track_stocks, track_by_id
from ..utils.common import setup_logging
from .signals import batch_signals

log = setup_logging("investlab.screener")


def screen_track(track_id: str, *, use_cache: bool = True) -> dict:
    meta = track_by_id(track_id)
    if not meta:
        return {"ok": False, "error": f"未知赛道 {track_id}"}
    if meta["tier"] == 3 and meta.get("parent_meta"):
        stocks = _extract_stocks(meta["stocks"]) or all_track_stocks().get(meta["parent"], [])
    else:
        stocks = all_track_stocks().get(track_id, []) if meta["tier"] == 2 else []
    quotes = get_quotes(stocks, use_cache=use_cache)
    signals = batch_signals(list({q for q in quotes}), use_cache=use_cache)
    rows = []
    by_sig = {s["symbol"]: s for s in signals}
    for sym in stocks:
        q = quotes.get(sym, {})
        sig = by_sig.get(sym, {})
        rows.append({
            "symbol": sym,
            "name": q.get("name", ""),
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "score": sig.get("score", 0),
            "stance": sig.get("stance", "-"),
            "gaps": sig.get("gaps", []),
            "source": q.get("source", ""),
        })
    rows.sort(key=lambda r: -r["score"])
    return {
        "ok": True,
        "track": {"id": track_id, "name": meta.get("name"),
                  "tier": meta["tier"],
                  "tam": meta.get("tam") or (meta.get("parent_meta") or {}).get("tam"),
                  "maturity": meta.get("maturity")
                  or (meta.get("parent_meta") or {}).get("maturity")},
        "rows": rows,
    }


def screen_portfolio_lenses() -> dict:
    """A/B 两类 + Top10 赛道的总览仪表数据。"""
    from ..tracks import load_taxonomy

    tax = load_taxonomy()
    lenses = {"A类·卖铲子(capex链)": [], "B类·用铲子(生产率)": []}
    for t in tax.get("secondary_tracks", []):
        bucket = "A类·卖铲子(capex链)" if t.get("class") == "A" else "B类·用铲子(生产率)"
        lenses[bucket].append({"id": t["id"], "name": t["name"],
                               "tam": t.get("tam"), "cagr": t.get("cagr"),
                               "maturity": t.get("maturity")})
    return {
        "lenses": lenses,
        "top_ranking": tax.get("top_ranking", []),
        "verification_metrics": tax.get("verification_metrics", []),
    }


def _extract_stocks(entries: list[str]) -> list[str]:
    from ..datasources.symbols import normalize

    out = []
    for e in entries or []:
        n = normalize(str(e).split()[0])
        if n:
            out.append(n)
    return out
