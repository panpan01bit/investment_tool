"""quantstats / PyPortfolioOpt 集成（quant/analytics）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from investlab.quant.analytics import (
    optimize,
    performance_metrics,
    prices_frame,
    rebalance_suggestions,
    tear_sheet_html,
)


def _returns(n=300, seed=7, mu=0.0008, sigma=0.015):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sigma, n),
                     index=pd.date_range("2025-01-01", periods=n))


def _prices(n=260, seed=1):
    dates = pd.date_range("2025-01-01", periods=n)
    cols = {}
    for i, (mu, sig) in enumerate([(0.001, 0.02), (0.0005, 0.015), (0.0015, 0.025)]):
        rng = np.random.default_rng(seed + i)
        cols[f"S{i}"] = 100 * np.cumprod(1 + rng.normal(mu, sig, n))
    return pd.DataFrame(cols, index=dates)


def test_performance_metrics_ok():
    m = performance_metrics(_returns())
    assert m["ok"] and m["days"] == 300
    for k in ("sharpe", "sortino", "max_drawdown_pct", "cagr_pct", "var_95_pct"):
        assert k in m and m[k] is not None
    assert -100 <= m["max_drawdown_pct"] <= 0


def test_performance_metrics_insufficient():
    m = performance_metrics(_returns()[:10])
    assert not m["ok"] and "不足" in m["error"]


def test_performance_metrics_drops_nan(isolated_env):
    r = _returns()
    r.iloc[5:20] = np.nan  # 15 个 NaN → 285 个有效样本
    m = performance_metrics(r)
    assert m["ok"] and m["days"] == 285


def test_tear_sheet_html(tmp_path):
    r = tear_sheet_html(_returns(), tmp_path / "ts.html")
    assert r["ok"] and r["size_kb"] > 50
    assert (tmp_path / "ts.html").is_file()


def test_optimize_all_methods():
    prices = _prices()
    for method in ("hrp", "max_sharpe", "min_volatility"):
        res = optimize(prices, method=method)
        assert res["ok"], f"{method}: {res.get('error')}"
        total = sum(res["weights"].values())
        assert abs(total - 1.0) < 0.01
        if method == "hrp":
            # HRP 无单标的权重上限约束（风险预算模型）
            assert all(0 <= w <= 1 for w in res["weights"].values())
        else:
            assert all(0 <= w <= 0.36 for w in res["weights"].values())  # ≤cap+余量


def test_optimize_rejects_bad_method():
    assert not optimize(_prices(), method="nope")["ok"]


def test_optimize_needs_two_assets():
    prices = _prices()[["S0"]]
    assert not optimize(prices, method="hrp")["ok"]


def test_optimize_insufficient_history():
    prices = _prices()[:80]  # <120 共同历史
    assert not optimize(prices, method="hrp")["ok"]


def test_prices_frame_coverage_filter():
    candles = {
        "A": [{"date": f"2025-01-{d:02d}", "close": 10 + d} for d in range(1, 20)],
        "B": [{"date": "2025-01-05", "close": 5}],  # 覆盖率过低的标的
    }
    df = prices_frame(candles)
    assert list(df.columns) == ["A"] or df.empty


def test_rebalance_suggestions_threshold():
    out = rebalance_suggestions({"A": 50, "B": 20, "C": 30},
                                {"A": 0.25, "B": 0.45, "C": 0.30})
    pairs = {x["symbol"]: x for x in out}
    assert "B" in pairs and pairs["B"]["action"] == "买入增持"
    assert pairs["A"]["diff_pct"] == pytest.approx(-25.0)
    assert all(abs(x["diff_pct"]) >= 3 for x in out)
