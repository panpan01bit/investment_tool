"""组合轮动回测：对我们观察池做"朴素版量化选股"（周频调仓、横截面打分）。

头部私募的全市场机器学习选股我们无法复制（数据/算力/执行），但可以借鉴其
公开可推断的框架——横截面打分 + 周频调仓 + 风格暴露管理——并用我们已有的
免费数据（K线+信号引擎）做一个透明的小型实现，用来回答：
  "这套风格在我们关注池里，当前周期到底有没有效？"

三种打分模式 + 可选"机制过滤"（全指60日动量<-3%的周持币防御，经实证）。
rotation_compare() 保证多模式对比共享同一数据快照（避免数据漂移污染结论）。
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from ..datasources.candles import get_candles
from ..utils.common import setup_logging

log = setup_logging("investlab.rotation")

MODES = ("momentum", "reversal", "signal")
REBALANCE_DAYS = 5          # 周频
COST_BPS = 15.0             # 单边成本（佣金+冲击）0.15%


def _zscore(series: pd.Series) -> pd.Series:
    s = series.astype(float)
    std = s.std()
    if not std or math.isnan(std):
        return s * 0
    return (s - s.mean()) / std


def score_universe(
    candles_by_symbol: dict[str, list[dict]],
    *,
    mode: str = "signal",
    asof_index: int | None = None,
) -> pd.DataFrame:
    """在某个时间截面上给池内标的打分（避免前视：只用 asof 及之前的数据）。"""
    rows = []
    for sym, candles in candles_by_symbol.items():
        data = candles if asof_index is None else candles[: asof_index + 1]
        if len(data) < 60:
            continue
        closes = [c["close"] for c in data]
        vols = [c.get("volume") or 0 for c in data]
        mom20 = closes[-1] / closes[-21] - 1 if len(closes) > 21 else np.nan
        rev5 = -(closes[-1] / closes[-6] - 1) if len(closes) > 6 else np.nan
        vol_trend = (
            (sum(vols[-5:]) / 5) / (sum(vols[-20:]) / 20)
            if sum(vols[-20:]) > 0 else np.nan
        )
        rows.append({
            "symbol": sym,
            "close": closes[-1],
            "mom20": mom20,
            "rev5": rev5,
            "vol_trend": vol_trend,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    if mode == "momentum":
        df["score"] = _zscore(df["mom20"])
    elif mode == "reversal":
        df["score"] = _zscore(df["rev5"])
    else:  # signal：动量与短反转各半，量能趋势微调（横截面内标准化）
        df["score"] = 0.45 * _zscore(df["mom20"]) + 0.35 * _zscore(df["rev5"]) \
            + 0.20 * _zscore(df["vol_trend"].fillna(1.0))
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def _regime_defensive_now() -> tuple[bool, str]:
    """当前是否处于防御期（factor_watch：全指60日动量<-3%）。"""
    try:
        from .factor_watch import factor_watch

        fw = factor_watch()
        styles = fw.get("styles", {})
        mom = (styles.get("momentum_60d") or {}).get("value")
        if mom is not None and mom < -0.03:
            return True, f"动量下行({mom:.1%})"
        return False, "正常"
    except Exception as exc:
        return False, f"regime不可得({exc})"


def _build_regime_by_date(dates: list[str], n: int,
                          regime_now: bool) -> dict[str, bool]:
    """历史逐周机制：全指 60 日动量滚动判定（只用周初及之前数据，无前视）。"""
    regime_by_date: dict[str, bool] = {}
    try:
        from .factor_watch import _index_series

        idx = _index_series("total", days=n + 80)
        closes = {r["date"]: r["close"] for r in idx}
        sorted_dates = sorted(closes)
        for d in dates:
            past = [v for dt in sorted_dates if dt <= d for v in [closes[dt]]]
            if len(past) >= 61:
                regime_by_date[d] = (past[-1] / past[-61] - 1) < -0.03
            else:
                # 指数历史不足（数据源限流等）→ 默认正常，不盲目防御
                regime_by_date[d] = False
    except Exception as exc:
        log.warning("历史机制构建失败，本轮回测不启用机制过滤: %s", exc)
        regime_by_date = {}
    return regime_by_date


def _run_backtest_on(
    aligned: dict[str, list[dict]],
    dates: list[str],
    n: int,
    *,
    mode: str,
    top_n: int,
    regime_by_date: dict[str, bool],
) -> dict:
    """在同一数据快照上执行周频轮动循环（纯计算，无IO）。"""
    symbols_aligned = list(aligned)
    equity = 1.0
    bench = 1.0
    holdings: list[str] = []
    pick_history: list[dict] = []
    weeks = 0
    trades = 0
    defensive_weeks = 0
    equity_series = [1.0] * n
    bench_series = [1.0] * n

    for start in range(60, n - 1, REBALANCE_DAYS):
        end = min(start + REBALANCE_DAYS, n - 1)
        scored = score_universe(aligned, mode=mode, asof_index=start)
        picks = scored.head(top_n)["symbol"].tolist()
        # 防御周（全指60日动量<-3%）：动量崩塌段最高动量组跌得最狠
        # （momentum crash），经实证持币是唯一有效的防御
        if regime_by_date.get(dates[start], False):
            defensive_weeks += 1
            if holdings:
                trades += len(holdings)
            holdings = []
            pick_history.append({"date": dates[start],
                                 "symbols": ["(现金·动量防御)"]})
            weeks += 1
            for i in range(start, end):
                equity_series[i] = equity
            continue
        if holdings:
            wk_rets = [
                aligned[sym][end]["close"] / aligned[sym][start]["close"] - 1
                for sym in holdings
            ]
            turnover = len(set(picks) - set(holdings)) / top_n
            wk_ret = sum(wk_rets) / len(wk_rets) - turnover * (COST_BPS / 10000) * 2
            equity *= 1 + wk_ret
            weeks += 1
            trades += len(set(picks) - set(holdings))
        pick_history.append({"date": dates[start], "symbols": picks})
        holdings = picks
        for i in range(start, end):
            equity_series[i] = equity
        bw_rets = [
            aligned[s][end]["close"] / aligned[s][start]["close"] - 1
            for s in symbols_aligned
        ]
        bench *= 1 + sum(bw_rets) / len(bw_rets)
        for i in range(start, end):
            bench_series[i] = bench

    equity_series[-1] = equity
    bench_series[-1] = bench

    def _metrics(series_vals: list[float]) -> dict:
        s = pd.Series(series_vals)
        rets = s.pct_change().dropna()
        years = n / 244
        cagr = (s.iloc[-1] ** (1 / years) - 1) if years > 0.5 else s.iloc[-1] - 1
        vol = rets.std() * math.sqrt(244) if len(rets) > 2 else float("nan")
        dd = (s / s.cummax() - 1).min()
        sharpe = (rets.mean() * 244 - 0.02) / vol if vol and vol == vol and vol > 0 else None
        return {
            "total_return_pct": round((float(s.iloc[-1]) - 1) * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "volatility_pct": round(vol * 100, 2) if vol == vol else None,
            "max_drawdown_pct": round(dd * 100, 2),
            "sharpe": round(sharpe, 2) if sharpe and sharpe == sharpe else None,
        }

    latest_picks = pick_history[-1] if pick_history else {"date": "", "symbols": []}
    return {
        "ok": True,
        "mode": mode,
        "universe_size": len(symbols_aligned),
        "top_n": top_n,
        "window_days": n,
        "start_date": dates[60],
        "defensive_weeks": defensive_weeks,
        "metrics": _metrics(equity_series),
        "bench_metrics": _metrics(bench_series),
        "weeks": weeks,
        "total_trades": trades,
        "curve": [
            {"date": dates[i], "strategy": round(equity_series[i], 4),
             "benchmark": round(bench_series[i], 4)}
            for i in range(0, n, max(1, n // 250))
        ],
        "latest_picks": latest_picks,
        "pick_history": pick_history[-12:],
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def _load_aligned(symbols: list[str], *, days: int,
                  use_cache: bool = True) -> tuple[dict[str, list[dict]], list[str], int] | dict:
    """拉数并对齐日期轴。失败返回 {"ok": False, "error": ...}。"""
    candles_by_symbol = {}
    min_len = 10**9
    for s in symbols:
        candles = get_candles(s, days=days, use_cache=use_cache)
        if len(candles) >= 80:
            candles_by_symbol[s] = candles
            min_len = min(min_len, len(candles))
    if len(candles_by_symbol) < 3:
        return {"ok": False,
                "error": f"有效K线标的不足（{len(candles_by_symbol)} 只）"}
    base = next(iter(candles_by_symbol.values()))
    n = min(min_len, len(base))
    dates = [c["date"] for c in base][-n:]
    aligned = {s: rows[-n:] for s, rows in candles_by_symbol.items() if len(rows) >= n}
    return aligned, dates, n


def rotation_backtest(
    symbols: list[str],
    *,
    mode: str = "signal",
    top_n: int = 5,
    days: int = 500,
    use_cache: bool = True,
    regime_filter: bool = False,
) -> dict:
    """周频调仓轮动回测。regime_filter=True 时防御周持币（实证有效的防御）。"""
    if mode not in MODES:
        return {"ok": False, "error": f"mode 须为 {MODES}"}
    if len(symbols) < top_n + 2:
        return {"ok": False, "error": f"池内标的至少需要 {top_n + 2} 只（当前 {len(symbols)}）"}

    loaded = _load_aligned(symbols, days=days, use_cache=use_cache)
    if isinstance(loaded, dict):
        return loaded
    aligned, dates, n = loaded

    regime_by_date: dict[str, bool] = {}
    if regime_filter:
        regime_by_date = _build_regime_by_date(dates, n, regime_now=False)

    res = _run_backtest_on(aligned, dates, n, mode=mode, top_n=top_n,
                           regime_by_date=regime_by_date)
    res["regime_filter"] = regime_filter
    res["regime_now"] = regime_by_date.get(dates[-1], False)
    return res


def rotation_compare(
    symbols: list[str],
    *,
    top_n: int = 5,
    days: int = 500,
    use_cache: bool = True,
    with_regime_filter: bool = True,
) -> dict:
    """多模式 × 机制过滤 的矩阵对比：同一数据快照，结论可比。"""
    loaded = _load_aligned(symbols, days=days, use_cache=use_cache)
    if isinstance(loaded, dict):
        return loaded
    aligned, dates, n = loaded
    regime_by_date = (_build_regime_by_date(dates, n, regime_now=False)
                      if with_regime_filter else {})

    results = []
    for mode in MODES:
        for rf in (False, True):
            if rf and not regime_by_date:
                continue
            res = _run_backtest_on(aligned, dates, n, mode=mode,
                                   top_n=top_n,
                                   regime_by_date=regime_by_date if rf else {})
            results.append({
                "mode": mode, "regime_filter": rf,
                "metrics": res["metrics"], "bench_metrics": res["bench_metrics"],
                "defensive_weeks": res["defensive_weeks"],
                "weeks": res["weeks"], "total_trades": res["total_trades"],
                "latest_picks": res["latest_picks"],
            })
    bench = results[0]["bench_metrics"] if results else {}
    return {
        "ok": True,
        "universe_size": len(aligned),
        "top_n": top_n,
        "window_days": n,
        "start_date": dates[60],
        "bench_metrics": bench,
        "results": results,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def default_universe(*, include_us: bool = False) -> list[str]:
    """默认池：投研档案观察池 ∪ 赛道代表标的（A股/港股）。"""
    try:
        from ..profiles import load_profile

        pool = list(load_profile().get("watchlist_seed") or [])
    except Exception:
        pool = []
    try:
        from ..tracks import all_track_stocks

        for stocks in all_track_stocks().values():
            pool += stocks
    except Exception:
        pass
    out = []
    seen = set()
    for s in pool:
        s = s.split()[0]
        if s in seen:
            continue
        from ..datasources.symbols import market_of

        if not include_us and market_of(s) == "US":
            continue
        seen.add(s)
        out.append(s)
    return out
