"""技术指标与信号引擎：合成数据上的数值断言。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from investlab.quant import indicators as ta
from investlab.quant.signals import compute_signals


def _synth_candles(n=300, seed=7, drift=0.0008):
    rng = np.random.default_rng(seed)
    price = 100 * np.cumprod(1 + rng.normal(drift, 0.02, n))
    df = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=n),
            "open": price,
            "high": price * 1.012,
            "low": price * 0.988,
            "close": price,
            "volume": rng.uniform(1e6, 5e6, n),
        }
    ).set_index("date")
    return [
        {"date": str(d.date()), "open": r.open, "high": r.high,
         "low": r.low, "close": r.close, "volume": r.volume}
        for d, r in df.iterrows()
    ]


def test_macd_shape_and_consistency():
    close = pd.Series([float(x) for x in range(1, 120)])
    m = ta.macd(close)
    assert len(m["dif"]) == len(close)
    # 单调上涨序列 DIF 应为正
    assert float(m["dif"].iloc[-1]) > 0


def test_rsi_bounds():
    rng = np.random.default_rng(3)
    s = pd.Series(100 + rng.normal(0, 2, 200).cumsum())
    r = ta.rsi(s).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_max_drawdown_known_value():
    s = pd.Series([100, 110, 90, 95])   # 高点110→低点90 = -18.18%
    dd = ta.max_drawdown(s)
    assert abs(dd - (90 / 110 - 1)) < 1e-6


def test_snapshot_contains_core_keys():
    snap = ta.snapshot_indicators(_synth_candles())
    for k in ("close", "rsi14", "ma20", "ma200", "macd_hist", "ann_vol"):
        assert k in snap and snap[k] is not None


def test_golden_cross_detection_synthetic():
    # 构造一段先跌后涨的序列，MA 交叉应可检测到金叉窗口
    up = list(np.linspace(80, 130, 60))
    down = list(np.linspace(130, 70, 120))
    close = pd.Series(down + up)
    dif = ta.ema(close, 12) - ta.ema(close, 26)
    dea = dif.ewm(span=9, adjust=False).mean()
    diff = dif - dea
    crossed = ((diff.shift(1) <= 0) & (diff > 0)).any()
    assert bool(crossed)


def test_compute_signals_with_insufficient_data(monkeypatch, isolated_env):
    monkeypatch.setattr(
        "investlab.quant.signals.get_candles", lambda s, days=280: []
    )
    rep = compute_signals("600519", use_cache=False)
    assert rep.stance == "数据不足"
    assert rep.gaps


def test_signal_score_bounded(monkeypatch, isolated_env):
    candles = _synth_candles(n=280, drift=0.004, seed=11)
    monkeypatch.setattr(
        "investlab.quant.signals.get_candles", lambda s, days=280: candles
    )
    monkeypatch.setattr("investlab.utils.common.cache_put", lambda *a, **k: None)
    rep = compute_signals("300308", use_cache=False)
    assert -100 <= rep.score <= 100
    assert rep.stance in ("偏多", "偏空", "震荡")
    assert all(r.direction in (-1, 0, 1) for r in rep.rules)
