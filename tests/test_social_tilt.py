"""社媒 tilt 计算与历史采集（合成历史，离线）。"""

from __future__ import annotations

from investlab.datasources import social
from investlab.quant import social_tilt as tilt_mod
from investlab.quant.social_tilt import social_tilt


def _seed_history(monkeypatch, heats: list[float]):
    rows = [{"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "heat": h}
            for i, h in enumerate(heats)]
    monkeypatch.setattr(tilt_mod, "heat_history", lambda sym: rows)


def test_tilt_rejects_insufficient_history(monkeypatch):
    _seed_history(monkeypatch, [50.0] * 30)  # < min_days 60
    assert social_tilt("NVDA") is None


def test_tilt_rising_heat_positive(monkeypatch):
    # 90 天从 20 线性升到 60：斜率为正且处于高位 → tilt 应为正
    heats = [20 + i * (40 / 89) for i in range(90)]
    _seed_history(monkeypatch, heats)
    res = social_tilt("NVDA")
    assert res is not None and res.coverage_days == 90
    assert res.tilt > 0.1
    assert not res.crowding


def test_tilt_falling_heat_negative(monkeypatch):
    heats = [60 - i * (40 / 89) for i in range(90)]
    _seed_history(monkeypatch, heats)
    res = social_tilt("NVDA")
    assert res is not None and res.tilt < -0.05


def test_tilt_crowding_zeroes_at_extreme(monkeypatch):
    # 长期 40 + 最近飙升到 92：水平 z 高、斜率正，但 ≥90 触发拥挤 → tilt=0
    heats = [40.0] * 80 + [60, 70, 80, 88, 92, 93, 94, 95, 96, 97]
    _seed_history(monkeypatch, heats)
    res = social_tilt("NVDA")
    assert res is not None and res.crowding
    assert res.tilt == 0.0
    assert any("拥挤" in n for n in res.notes)


def test_tilt_stable_history_near_zero(monkeypatch):
    heats = [50.0 + (i % 3) for i in range(120)]  # 无趋势窄幅波动
    _seed_history(monkeypatch, heats)
    res = social_tilt("NVDA")
    assert res is not None and abs(res.tilt) < 0.35


def test_record_snapshots_dedupes_per_day(monkeypatch, isolated_env):
    calls = []

    def fake_pulse(sym):
        calls.append(sym)
        return {"heat": 42.0, "heat_label": "升温", "query": sym,
                "items": [], "source_status": {"reddit": "ok"}}

    monkeypatch.setattr(social, "pulse_for_symbol", fake_pulse)
    r1 = social.record_snapshots(["NVDA", "300308.SZ"])
    r2 = social.record_snapshots(["NVDA"])  # 同日重复 → 跳过
    assert {r["symbol"] for r in r1} == {"NVDA", "300308.SZ"}
    assert r2 == []
    assert len(calls) == 2  # 第二次完全不再触发网络

    hist = social.heat_history("NVDA")
    assert len(hist) == 1 and hist[0]["heat"] == 42.0
