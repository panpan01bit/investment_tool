"""社媒热度 tilt：把日采集的 heat 历史 → 策略可用的 -1~1 事件倾斜值。

面向策略 0030 的接入点（见 docs/SOCIAL_TILT_INTEGRATION.md）：
  adj_zc += p_trend × λ_social × social_tilt(symbol)      # λ_theme 的并行项/替代项
  拥挤度风控：heat ≥ 90（白热化）时不加仓、已持有减半

诚实纪律：
- 历史不足 min_days 天 → 返回 None（覆盖不足，绝不给数）；
- tilt 由 heat 水平 z 分与 7 日斜率 z 分合成，各占一半；
- 白热化时 tilt 强制归零并带 crowding 标记（拥挤=减仓不加仓）；
- 本模块不承诺收益改善——任何数字须经策略自身的 clean ablation 验证。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..datasources.social import heat_history
from ..utils.common import setup_logging

log = setup_logging("investlab.social_tilt")


@dataclass
class TiltResult:
    symbol: str
    tilt: float                # -1 ~ +1
    heat: float                # 最新 heat
    heat_z: float              # 水平 z 分（相对自身历史）
    slope_z: float             # 7 日变化斜率 z 分
    coverage_days: int
    crowding: bool = False     # heat 白热化 → 拥挤警示
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _zscore(values: list[float], x: float) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    if std < 1e-9:
        return 0.0
    return max(-3.0, min(3.0, (x - mean) / std))


def social_tilt(symbol: str, *, min_days: int = 60) -> TiltResult | None:
    """从日采集历史计算 tilt。历史 < min_days 返回 None。"""
    s = symbol
    rows = heat_history(s)
    heats = [float(r["heat"]) for r in rows if r.get("heat") is not None]
    if len(heats) < min_days:
        return None

    latest = heats[-1]
    heat_z = _zscore(heats, latest)

    # 7 日斜率（heat/日），再对历史斜率序列取 z 分
    slopes = []
    for i in range(6, len(heats)):
        slopes.append((heats[i] - heats[i - 6]) / 6.0)
    slope_now = slopes[-1] if slopes else 0.0
    slope_z = _zscore(slopes, slope_now) if len(slopes) >= 10 else 0.0

    tilt = max(-1.0, min(1.0, 0.5 * heat_z / 3.0 + 0.5 * slope_z / 3.0))
    crowding = latest >= 90.0
    notes = []
    if crowding:
        notes.append("heat≥90 白热化：拥挤警示，tilt 归零（只减不加）")
        tilt = 0.0
    if len(heats) < 120:
        notes.append(f"历史仅 {len(heats)} 日，统计不稳，仅作研究参考")
    return TiltResult(
        symbol=s,
        tilt=round(tilt, 3),
        heat=round(latest, 1),
        heat_z=round(heat_z, 2),
        slope_z=round(slope_z, 2),
        coverage_days=len(heats),
        crowding=crowding,
        notes=notes,
    )
