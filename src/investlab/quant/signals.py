"""信号引擎：把技术指标合成 -100~100 复合分 + 有理由的结论。

设计原则（参考 UZI-Skill）：
- 每条规则给出方向、权重与人类可读理由；
- 数据缺失时该规则直接跳过并记入 gaps，绝不编造；
- 输出可 JSON 化，供前端和简报使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..datasources.candles import get_candles
from ..utils.common import cache_get, cache_put, now_cn, setup_logging
from . import indicators as ta

log = setup_logging("investlab.signals")

SIGNALS_TTL_S = 30 * 60.0


@dataclass
class RuleHit:
    name: str
    direction: int          # +1 看多 / -1 看空 / 0 中性
    weight: int             # 1~3
    reason: str


@dataclass
class SignalReport:
    symbol: str
    score: int              # -100 ~ +100
    stance: str             # 偏多 / 偏空 / 震荡 / 数据不足
    rules: list[RuleHit] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    ts: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": self.score,
            "stance": self.stance,
            "rules": [
                {"name": r.name, "direction": r.direction,
                 "weight": r.weight, "reason": r.reason}
                for r in self.rules
            ],
            "indicators": self.indicators,
            "gaps": self.gaps,
            "ts": self.ts or now_cn().isoformat(timespec="seconds"),
        }


def compute_signals(symbol: str, *, use_cache: bool = True, days: int = 280) -> SignalReport:
    s = symbol
    key = [s]
    if use_cache:
        cached = cache_get("signals_v1", key, SIGNALS_TTL_S)
        if cached:
            rep = SignalReport(symbol=s, score=cached["score"], stance=cached["stance"])
            rep.rules = [RuleHit(**r) for r in cached["rules"]]
            rep.indicators = cached.get("indicators", {})
            rep.gaps = cached.get("gaps", [])
            rep.ts = cached["ts"]
            return rep

    candles = get_candles(s, days=days)
    if len(candles) < 60:
        rep = SignalReport(symbol=s, score=0, stance="数据不足", ts=now_cn().isoformat(timespec="seconds"))
        rep.gaps.append(f"K线不足({len(candles)}根)，无法计算有效信号")
        return rep

    snap = ta.snapshot_indicators(candles)
    rules: list[RuleHit] = []

    def add(name, cond, direction, weight, reason):
        if cond is None:
            return
        rules.append(RuleHit(name=name, direction=direction if cond else 0,
                             weight=weight if cond else 0, reason=reason))

    # ---- 趋势类
    ma_bull = snap.get("ma_bull_alignment")
    add("均线多头排列", ma_bull, +1, 2, "MA5>MA10>MA20，短期趋势向上" if ma_bull else "")
    add("均线空头排列",
        (False if ma_bull is None else (not ma_bull)) and _lt(snap.get("ma5"), snap.get("ma20")),
        -1, 2, "MA5<MA20，短期趋势向下")
    stage = snap.get("weinstein_stage_guess")
    add("韦恩斯坦第二阶段", stage and "第二阶段" in str(stage), +1, 3,
        f"价格站上MA200且年线拐头向上：{stage}")
    add("韦恩斯坦第四阶段", stage and "第四阶段" in str(stage), -1, 3,
        f"价格跌破MA200且年线下行：{stage}")

    # ---- 动能类
    hist = snap.get("macd_hist")
    add("MACD红柱", None if hist is None else hist > 0, +1, 1, f"DIF-DEA柱为正({hist})")
    gc = snap.get("macd_golden_cross_recent")
    add("MACD近期金叉", gc, +1, 2, "近5日DIF上穿DEA")
    rsi14 = snap.get("rsi14")
    add("RSI超卖回升", None if rsi14 is None else rsi14 < 32, +1, 1, f"RSI={rsi14}，短线超卖")
    add("RSI超买预警", None if rsi14 is None else rsi14 > 78, -1, 1, f"RSI={rsi14}，短线过热")
    kdj_j = snap.get("kdj_j")
    add("KDJ-J值超卖", None if kdj_j is None else kdj_j < 15, +1, 1, f"J={kdj_j}")

    # ---- 位置 / 量能
    dist_high = snap.get("dist_52w_high_pct")
    add("临近52周高点", None if dist_high is None else (-3 <= dist_high <= 8),
        +1, 1, f"距52周高点{dist_high}%，突破窗口")
    vol_ratio = snap.get("volume_vs_ma20")
    add("放量异动", None if vol_ratio is None else vol_ratio >= 1.8,
        0, 1, f"当日量为20日均量的{vol_ratio}倍，需结合涨跌方向确认")
    obv_slope = snap.get("obv_slope20")
    add("OBV资金流入", None if obv_slope is None else obv_slope > 0.02,
        +1, 1, f"20日OBV斜率{obv_slope}")

    active = [(r, i) for i, r in enumerate(rules)]
    longs = sum(r.weight * r.direction for r, _ in active if r.direction == 1)
    shorts = sum(r.weight * abs(r.direction) for r, _ in active if r.direction == -1)
    total_w = sum(max(1, r.weight) for r, _ in active if r.direction != 0) or 1
    raw_score = int(round(100 * (longs - shorts) / max(total_w * 3, 1)))
    score = max(-100, min(100, raw_score))
    stance = _stance(score)

    rep = SignalReport(
        symbol=s,
        score=score,
        stance=stance,
        rules=[r for r, _ in active],
        indicators=snap,
        gaps=_collect_gaps(snap),
        ts=now_cn().isoformat(timespec="seconds"),
    )
    cache_put("signals_v1", key, rep.to_dict(), ttl_s=SIGNALS_TTL_S)
    return rep


def _stance(score: int) -> str:
    if score >= 55:
        return "偏多"
    if score <= -40:
        return "偏空"
    return "震荡"


def _lt(a, b):
    """a < b，任一缺失返回 None。"""
    if a is None or b is None:
        return None
    return a < b


def _collect_gaps(snap: dict) -> list[str]:
    gaps = []
    label_map = {
        "ma200": "MA200样本不足(<200日)",
        "weinstein_stage_guess": "年线阶段不可判",
        "ann_vol": "波动率不可算",
        "boll_pos": "布林带位置缺失",
    }
    for k, label in label_map.items():
        v = snap.get(k)
        if v is None or (isinstance(v, float) and v != v):
            gaps.append(label)
    return gaps


# ------------------------------------------------------------------ 批量


def batch_signals(symbols: list[str], *, use_cache: bool = True) -> list[dict]:
    out = []
    for s in symbols:
        try:
            out.append(compute_signals(s, use_cache=use_cache).to_dict())
        except Exception as exc:
            log.warning("信号计算失败 %s: %s", s, exc)
            out.append({"symbol": s, "score": 0, "stance": "数据不足",
                        "rules": [], "indicators": {},
                        "gaps": [f"计算异常: {exc}"],
                        "ts": now_cn().isoformat(timespec="seconds")})
    return sorted(out, key=lambda d: -d.get("score", 0))
