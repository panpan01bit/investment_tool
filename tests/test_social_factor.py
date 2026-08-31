"""社媒关注度因子：序列构造与预测检验（mock HN Algolia，离线）。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def mock_hn(monkeypatch):
    """HN Algolia nbHits 按周返回确定序列（先升后降的正弦形态）。"""
    import math

    calls = []

    def fake_get_json(url, *, params=None, timeout=12, retries=2, **kw):
        nf = (params or {}).get("numericFilters", "")
        calls.append(nf)
        # 从 numericFilters 提取周起点，用正弦生成确定性计数
        t0 = int(nf.split(">")[1].split(",")[0])
        import datetime as dt

        week = int(dt.datetime.fromtimestamp(t0, dt.timezone.utc).strftime("%V"))
        count = int(50 + 40 * math.sin(week / 3.0) + week)
        return {"nbHits": count}

    from investlab.quant import social_factor as sf

    monkeypatch.setattr(sf, "http_get_json", fake_get_json)
    return calls


def test_weekly_mentions_shape(mock_hn, isolated_env):
    from investlab.quant.social_factor import hn_weekly_mentions

    rows = hn_weekly_mentions("NVIDIA", weeks=30)
    assert len(rows) == 30
    assert all("week_start" in r and "mentions" in r for r in rows)
    assert rows[0]["week_start"] < rows[-1]["week_start"]
    assert len(mock_hn) == 30


def test_weekly_mentions_cached(monkeypatch, isolated_env):
    from investlab.quant.social_factor import hn_weekly_mentions

    n_calls = {"n": 0}

    def fake(url, **kw):
        n_calls["n"] += 1
        return {"nbHits": 5}

    monkeypatch.setattr("investlab.quant.social_factor.http_get_json", fake)
    hn_weekly_mentions("cachedq", weeks=10)
    hn_weekly_mentions("cachedq", weeks=10)
    assert n_calls["n"] == 10  # 第二轮全部命中缓存


def test_factor_series_fields(mock_hn, isolated_env):
    from investlab.quant.social_factor import social_factor_series

    res = social_factor_series("NVIDIA", weeks=40)
    assert res["ok"]
    assert len(res["weeks"]) == 40
    w = res["weeks"][-1]
    assert {"week_start", "mentions", "level_z", "accel"} <= set(w)
    # 早期窗口样本不足 → level_z 为 None；后期必有值
    assert res["weeks"][-1]["level_z"] is not None


def test_analyze_rejects_insufficient_history(isolated_env, monkeypatch):
    from investlab.quant import social_factor as sf

    monkeypatch.setattr(sf, "hn_weekly_mentions",
                        lambda q, weeks, use_cache=True: [
                            {"week_start": "2026-08-01", "mentions": 3}])
    res = sf.analyze_social_factor("NVIDIA", ["NVDA"], weeks=78)
    assert not res["ok"] and "不足" in res["error"]


def test_analyze_end_to_end_mocked(isolated_env, monkeypatch):
    """端到端：mock 周度计数 + 合成池K线 → 统计字段齐全。"""
    import math

    from investlab.quant import social_factor as sf

    def fake_weekly(query, weeks, use_cache=True):
        rows = []
        monday0 = None
        import datetime as dt

        now = dt.datetime.now(dt.timezone.utc)
        this_monday = (now - dt.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for w in range(weeks - 1, -1, -1):
            monday = this_monday - dt.timedelta(days=7 * w)
            if monday0 is None:
                monday0 = monday
            idx_week = int(monday.strftime("%V"))
            count = int(100 + 60 * math.sin(idx_week / 2.5))
            rows.append({"week_start": monday.date().isoformat(), "mentions": count})
        return rows

    monkeypatch.setattr(sf, "hn_weekly_mentions", fake_weekly)

    # 合成池：三只同涨跌+微差
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(5)
    n = 500
    dates = [d.strftime("%Y-%m-%d")
             for d in pd.date_range("2024-08-01", periods=n)]
    base = 100 * np.cumprod(1 + rng.normal(0.002, 0.015, n))
    candles = {}
    for i, sym in enumerate(("A", "B", "C")):
        px = base * (1 + i * 0.01)
        candles[sym] = [
            {"date": d, "open": p, "high": p, "low": p, "close": p, "volume": 1e6}
            for d, p in zip(dates, px, strict=False)
        ]
    monkeypatch.setattr(
        "investlab.datasources.candles.get_candles",
        lambda s, days=520, use_cache=True: candles[s],
    )
    res = sf.analyze_social_factor("NVIDIA", ["A", "B", "C"], weeks=78)
    assert res["ok"], res.get("error")
    assert res["n_weeks"] >= 20
    assert "orth_ic_residual" in res["predictive"]
    assert {"low_attention", "mid", "high_attention"} <= set(res["tercile_next_week_ret_pct"])
    assert "strategy_attention_timing" in res and "buyhold" in res
