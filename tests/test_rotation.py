"""轮动回测与风格机制（合成数据，离线）。"""

from __future__ import annotations

import pandas as pd

from investlab.quant import rotation as rot


def _aligned_pool(n=300, n_sym=6, seed=0):
    """构造 n_sym 只、n 根K线的合成池：动量斜率差异远大于噪声，保证排序确定。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    aligned = {}
    for i in range(n_sym):
        drift = 0.004 - i * 0.001            # S0最强 … S5最弱（月度差>噪声3倍）
        price = 50 * np.cumprod(1 + rng.normal(drift, 0.003, n))
        aligned[f"S{i}"] = [
            {"date": d, "open": p, "high": p * 1.005, "low": p * 0.995,
             "close": p, "volume": 1e6}
            for d, p in zip(dates, price, strict=False)
        ]
    return aligned, list(dates), n


def test_score_universe_ranks_by_momentum():
    aligned, _, n = _aligned_pool()
    scored = rot.score_universe(aligned, mode="momentum", asof_index=n - 1)
    assert scored.iloc[0]["symbol"] == "S0"     # 动量最强在首
    assert scored.iloc[-1]["symbol"] == "S5"
    assert abs(scored["score"].mean()) < 1e-6   # 横截面z分均值≈0


def test_score_universe_no_lookahead():
    """用 asof 截面打分必须与"只用前段数据"一致。"""
    aligned, _, n = _aligned_pool()
    a = rot.score_universe(aligned, mode="reversal", asof_index=150)
    truncated = {s: rows[:151] for s, rows in aligned.items()}
    b = rot.score_universe(truncated, mode="reversal", asof_index=None)
    assert list(a["symbol"]) == list(b["symbol"])


def test_run_backtest_defensive_weeks_hold_cash():
    aligned, dates, n = _aligned_pool()
    # 人为把全部周设为防御 → 策略净值恒为1（持币）
    regime_by_date = {d: True for d in dates}
    res = rot._run_backtest_on(aligned, dates, n, mode="momentum",
                               top_n=3, regime_by_date=regime_by_date)
    assert res["ok"]
    assert res["defensive_weeks"] == res["weeks"]
    assert res["metrics"]["total_return_pct"] == 0.0
    assert all(p["symbols"] == ["(现金·动量防御)"] for p in res["pick_history"])


def test_run_backtest_normal_weeks_hold_top():
    aligned, dates, n = _aligned_pool()
    res = rot._run_backtest_on(aligned, dates, n, mode="momentum",
                               top_n=3, regime_by_date={})
    assert res["ok"] and res["weeks"] > 0
    # 动量模式应长期持有动量最强的S0/S1
    held = {s for p in res["pick_history"] for s in p["symbols"]}
    assert {"S0", "S1"} & held


def test_run_backtest_benchmark_is_equal_weight():
    aligned, dates, n = _aligned_pool(n_sym=4)
    res = rot._run_backtest_on(aligned, dates, n, mode="reversal",
                               top_n=2, regime_by_date={})
    # 无成本近似下，等权基准收益应接近池均值；只验证结构
    assert "bench_metrics" in res and "total_return_pct" in res["bench_metrics"]


# ------------------------------------------------------------------ 机制判定


def test_regime_by_date_builder(monkeypatch):
    from investlab.quant import factor_watch as fwmod

    dates = ["2024-11-20", "2024-11-25", "2025-01-15"]
    # 指数：10月28日(100) + 11月28日(95) + 12月28日(90)
    idx = [
        {"date": f"2024-{m:02d}-{d:02d}", "close": c, "volume": 0, "amount": 0}
        for m, c in ((10, 100), (11, 95), (12, 90))
        for d in range(1, 29)
    ]
    monkeypatch.setattr(fwmod, "_index_series", lambda key, days: idx)
    regime = rot._build_regime_by_date(dates, len(dates), regime_now=False)
    # 11-20: 历史48日<60 → 默认正常False
    assert regime["2024-11-20"] is False
    # 11-25: 历史53日<60 → False
    assert regime["2024-11-25"] is False
    # 01-15: 99日历史，60日动量=90/95-1≈-5.3% → 防御True
    assert regime["2025-01-15"] is True


def test_insufficient_index_defaults_normal(monkeypatch):
    from investlab.quant import factor_watch as fwmod

    dates = ["2025-01-01", "2025-01-02"]
    monkeypatch.setattr(fwmod, "_index_series", lambda key, days: [])
    regime = rot._build_regime_by_date(dates, 2, regime_now=True)
    # 指数历史不足 → 默认正常（不盲目防御）
    assert all(v is False for v in regime.values())
