"""回测引擎与组合分析。"""

from __future__ import annotations

import csv

import numpy as np
import pandas as pd

from investlab.obsidian.vault import new_vault
from investlab.quant.backtest import run_backtest
from investlab.quant.portfolio import load_holdings, write_holdings_csv


def _candles(n=400, seed=5, drift=0.001):
    rng = np.random.default_rng(seed)
    price = 50 * np.cumprod(1 + rng.normal(drift, 0.015, n))
    dates = pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    return [
        {"date": d, "open": p, "high": p * 1.01,
         "low": p * 0.99, "close": p, "volume": 1e6}
        for d, p in zip(dates, price, strict=False)
    ]


def test_backtest_metrics_sanity(monkeypatch, isolated_env):
    candles = _candles()
    monkeypatch.setattr(
        "investlab.quant.backtest.get_candles", lambda s, days=500: candles
    )
    monkeypatch.setattr("investlab.utils.common.cache_put", lambda *a, **k: None)
    res = run_backtest("600519", strategy="sma_cross", days=500)
    assert res["ok"]
    m = res["metrics"]
    assert m["trades"] >= 0
    assert len(res["curve"]) == 400
    assert m["total_return_pct"] < 10000 and m["total_return_pct"] > -100
    # 策略净值曲线字段齐全
    assert {"date", "strategy", "benchmark"} <= set(res["curve"][0].keys())


def test_backtest_unknown_strategy(monkeypatch, isolated_env):
    candles = _candles()
    monkeypatch.setattr(
        "investlab.quant.backtest.get_candles", lambda s, days=500: candles
    )
    try:
        run_backtest("600519", strategy="nope")
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_backtest_insufficient_data(monkeypatch, isolated_env):
    monkeypatch.setattr(
        "investlab.quant.backtest.get_candles", lambda s, days=500: []
    )
    res = run_backtest("600519")
    assert not res["ok"] and "数据不足" in res["error"]


# ------------------------------------------------------------------ 组合


def test_holdings_roundtrip(isolated_env):
    new_vault().ensure_layout()
    rows = [
        {"symbol": "300308.SZ", "name": "中际旭创", "quantity": 100,
         "cost_price": 120.5, "currency": "CNY", "category": "光模块"},
        {"symbol": "002837.SZ", "name": "英维克", "quantity": 200,
         "cost_price": 30.0, "currency": "CNY", "category": "液冷"},
        {"symbol": "BAD/PATH", "name": "x", "quantity": 1},
    ]
    write_holdings_csv(rows)
    loaded = load_holdings()
    syms = [h["symbol"] for h in loaded]
    assert "300308.SZ" in syms and "002837.SZ" in syms
    assert all("/" not in s for s in syms)


def test_run_filter_excludes(isolated_env):
    import io

    new_vault().ensure_layout()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["symbol", "quantity", "run"])
    w.writeheader()
    w.writerow({"symbol": "600519", "quantity": 10, "run": "Y"})
    w.writerow({"symbol": "000333", "quantity": 20, "run": "N"})
    new_vault().holdings_path().write_text(buf.getvalue(), encoding="utf-8")
    loaded = load_holdings()
    assert [h["symbol"] for h in loaded] == ["600519.SS"]
