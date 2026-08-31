"""社媒关注度因子：用 HN Algolia 的历史日期过滤回溯重建周度提及量，
使社媒因子"今天就能回测"（无需等待自建数据积累）。

与 social_tilt 的关系：
- social_tilt = 个股级、需要 ≥60 天自采历史（从今天起积累）→ 实盘倾斜用
- social_factor = 主题级、HN 历史可回溯 → 研究回测用（本模块）

因子构造（对某主题 q，周度）：
  mentions_t      = HN 该周提及帖子数（nbHits）
  level_z         = log1p(mentions) 的滚动 z 分
  accel           = 周环比变化率（关注度加速/退潮）
  regime_signal   = accel > 0 → 持有主题池；否则防御

诚实纪律：预测性检验必须用 heat_t → ret_{t+1,t+5}（滞后一期），
同时给出同期相关（co-movement）以便区分"同步共振"与"领先预测"。
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..netguard import http_get_json
from ..utils.common import cache_get, cache_put, setup_logging

log = setup_logging("investlab.social_factor")

_WEEK = timedelta(days=7)
_CACHE_TTL = 24 * 3600.0


def hn_weekly_mentions(query: str, weeks: int = 60, *, use_cache: bool = True) -> list[dict]:
    """回溯 weeks 周，每周该主题在 HN 的故事提及数。

    返回升序 [{week_start: 'YYYY-MM-DD', mentions: int}]（week_start=周一）。
    """
    query = (query or "").strip()
    if not query:
        return []
    out = []
    now = datetime.now(timezone.utc)
    # 从当前周往前 weeks 周
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    for w in range(weeks - 1, -1, -1):
        start = this_monday - _WEEK * w
        end = start + _WEEK
        ckey = ["hn_weekly", query, start.date().isoformat()]
        if use_cache:
            cached = cache_get("social_factor_v1", ckey, _CACHE_TTL)
            if cached is not None:
                out.append({"week_start": start.date().isoformat(),
                            "mentions": int(cached)})
                continue
        t0 = int(start.timestamp())
        t1 = int(end.timestamp())
        data = http_get_json(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query,
                "tags": "story",
                "hitsPerPage": 0,
                "numericFilters": f"created_at_i>{t0},created_at_i<{t1}",
            },
            timeout=12, retries=2,
        )
        count = int((data or {}).get("nbHits") or 0)
        cache_put("social_factor_v1", ckey, count, ttl_s=_CACHE_TTL)
        out.append({"week_start": start.date().isoformat(), "mentions": count})
        time.sleep(0.25)  # Algolia 友好限速
    return out


def _log_z(counts: list[int]) -> list[float]:
    return [math.log1p(max(c, 0)) for c in counts]


def _rolling_z(vals: list[float], window: int = 26) -> list[float | None]:
    """滚动窗口 z 分（用截至当期的历史，避免前视）。"""
    out: list[float | None] = []
    for i in range(len(vals)):
        hist = vals[max(0, i - window):i]
        if len(hist) < 10:
            out.append(None)
            continue
        mean = sum(hist) / len(hist)
        var = sum((x - mean) ** 2 for x in hist) / len(hist)
        std = math.sqrt(var)
        out.append(0.0 if std < 1e-9 else max(-3, min(3, (vals[i] - mean) / std)))
    return out


def social_factor_series(query: str, weeks: int = 60, *,
                         use_cache: bool = True) -> dict:
    """因子序列：mentions / level_z / accel（周环比变化率）。"""
    weekly = hn_weekly_mentions(query, weeks=weeks, use_cache=use_cache)
    if len(weekly) < 20:
        return {"ok": False, "error": f"提及历史不足（{len(weekly)}周）", "query": query}
    counts = [w["mentions"] for w in weekly]
    logs = _log_z(counts)
    level_z = _rolling_z(logs, 26)
    accel = [None] * len(counts)
    for i in range(1, len(counts)):
        prev = counts[i - 1]
        accel[i] = None if prev <= 0 else (counts[i] - prev) / prev
    return {
        "ok": True,
        "query": query,
        "weeks": [
            {
                "week_start": weekly[i]["week_start"],
                "mentions": counts[i],
                "level_z": level_z[i],
                "accel": None if accel[i] is None else round(accel[i], 3),
            }
            for i in range(len(weekly))
        ],
    }


# ------------------------------------------------------------------ 回测验证


def _pool_weekly_returns(symbols: list[str], dates: list[str],
                         closes_aligned: dict[str, list[float]],
                         week_start_idx: list[int]) -> list[float | None]:
    """池等权周收益：week_start_idx 为每周起始K线索引，收益= idx→idx+5。"""
    rets: list[float | None] = []
    n = len(dates)
    for i in week_start_idx:
        j = min(i + 5, n - 1)
        if i >= n - 1:
            rets.append(None)
            continue
        rs = [closes_aligned[s][j] / closes_aligned[s][i] - 1
              for s in symbols if closes_aligned.get(s)]
        rets.append(sum(rs) / len(rs) if rs else None)
    return rets


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """秩相关（预测IC的稳健版本）。"""
    if len(xs) < 10 or len(xs) != len(ys):
        return None

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rk, i in enumerate(order):
            r[i] = float(rk)
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=False))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx < 1e-9 or dy < 1e-9:
        return None
    return num / (dx * dy)


def analyze_social_factor(
    query: str,
    symbols: list[str],
    *,
    weeks: int = 78,
    use_cache: bool = True,
) -> dict:
    """主题社媒关注度 → 主题池前瞻收益 的预测力检验。

    设计：
    - 因子周（自然周）对齐到池的调仓周（池周起始日所在自然周）；
    - 预测性：corr(heat_t, ret_{t→t+5}) 与 Spearman IC（滞后一期，无前视）；
    - 同步性：corr(heat_t, ret 同期) —— 用于区分共振与预测；
    - 策略对比：关注度加速(accel>0)时持有池，否则持币 vs 买入持有。
    """
    from ..datasources.candles import get_candles

    fac = social_factor_series(query, weeks=weeks, use_cache=use_cache)
    if not fac.get("ok"):
        return fac
    series = {w["week_start"]: w for w in fac["weeks"]}

    # 池数据（对齐）
    candles = {}
    min_len = 10**9
    for s in symbols:
        c = get_candles(s, days=520, use_cache=True)
        if len(c) >= 80:
            candles[s] = c
            min_len = min(min_len, len(c))
    if len(candles) < 3:
        return {"ok": False, "error": "池内有效K线不足"}
    base = next(iter(candles.values()))
    all_dates = [c["date"] for c in base][-min_len:]
    closes_aligned = {
        s: [float(c["close"]) for c in rows[-min_len:]]
        for s, rows in candles.items()
    }
    syms = list(candles)

    # 池的调仓周起点（每5个交易日），映射到自然周
    week_start_idx = list(range(60, min_len - 1, 5))
    pairs = []  # (week_monday, factor, fwd_ret, same_ret)
    for i in week_start_idx:
        d = all_dates[i]
        monday = (datetime.strptime(d, "%Y-%m-%d")
                  - timedelta(days=datetime.strptime(d, "%Y-%m-%d").weekday())
                  ).date().isoformat()
        w = series.get(monday)
        if not w:
            continue
        j = min(i + 5, min_len - 1)
        rs = [closes_aligned[s][j] / closes_aligned[s][i] - 1 for s in syms]
        fwd = sum(rs) / len(rs)
        pairs.append({
            "date": d, "monday": monday,
            "mentions": w["mentions"],
            "level_z": w["level_z"],
            "accel": w["accel"],
            "fwd_ret": fwd,
            "same_ret": fwd,  # 占位：同期=同段收益
        })

    if len(pairs) < 20:
        return {"ok": False, "error": f"对齐样本不足（{len(pairs)}周）"}

    def _corr(xs, ys):
        n = len(xs)
        if n < 10:
            return None
        mx, my = sum(xs) / n, sum(ys) / n
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=False))
        dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
        dy = math.sqrt(sum((b - my) ** 2 for b in ys))
        if dx < 1e-9 or dy < 1e-9:
            return None
        return num / (dx * dy)

    # 预测性：heat_t → 下一周收益（t+1周）
    predictive = [
        (p["level_z"], q["fwd_ret"])
        for p, q in zip(pairs, pairs[1:], strict=False)
        if p["level_z"] is not None and q is not None
    ]
    predictive_accel = [
        (p["accel"], q["fwd_ret"])
        for p, q in zip(pairs, pairs[1:], strict=False)
        if p["accel"] is not None
    ]
    lvl = [a for a, _ in predictive]
    fwd1 = [b for _, b in predictive]
    acc = [a for a, _ in predictive_accel]
    fwd1b = [b for _, b in predictive_accel]

    ic_level = _spearman(lvl, fwd1)
    ic_accel = _spearman(acc, fwd1b) if len(acc) >= 10 else None
    corr_level = _corr(lvl, fwd1)
    same_pairs = [(p["level_z"], p["fwd_ret"]) for p in pairs if p["level_z"] is not None]
    corr_same = _corr([a for a, _ in same_pairs], [b for _, b in same_pairs])

    # 正交化：把热度对同期收益回归，取因子残差（剔除"关注度跟随价格"污染），
    # 再检验残差对未来一周收益的IC —— 头部处理舆情因子的标准姿势
    orth_ic = None
    if len(same_pairs) >= 20:
        xs = [a for a, _ in same_pairs]                       # level_z
        ys = [b for _, b in same_pairs]                       # 同期收益
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        var_y = sum((b - my) ** 2 for b in ys)
        beta = (sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=False)) / var_y
                if var_y > 1e-12 else 0.0)
        resids = [a - beta * (b - my) for a, b in zip(xs, ys, strict=False)]
        nxt = [q["fwd_ret"] for q in pairs[1:]
               if pairs[pairs.index(q) - 1]["level_z"] is not None]
        m = min(len(resids), len(nxt))
        if m >= 20:
            orth_ic = _spearman(resids[:m], nxt[:m])

    # 三分位：高/中/低关注周的下一周平均收益
    next_ret_by_monday = {
        p["monday"]: q["fwd_ret"] for p, q in zip(pairs, pairs[1:], strict=False)
    }
    valid = sorted([p for p in pairs if p["level_z"] is not None],
                   key=lambda p: p["level_z"])
    t = max(1, len(valid) // 3)

    def _avg_fwd(subset):
        vals = [next_ret_by_monday.get(s["monday"]) for s in subset[:-1]]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    low_ret = _avg_fwd(valid[:t])
    mid_ret = _avg_fwd(valid[t:2 * t])
    high_ret = _avg_fwd(valid[2 * t:])

    # 策略：关注度加速>0 → 持有池；否则持币（周频，滞后一期使用）
    equity = bench = 1.0
    eq_curve, bench_curve = [], []
    for p, q in zip(pairs, pairs[1:], strict=False):
        hold = (p["accel"] is not None and p["accel"] > 0)
        if hold:
            equity *= 1 + q["fwd_ret"]
        bench *= 1 + q["fwd_ret"]
        eq_curve.append(equity)
        bench_curve.append(bench)
    def _quick_metrics(curve):
        s = pd.Series(curve)
        rets = s.pct_change().dropna()
        dd = (s / s.cummax() - 1).min()
        vol = rets.std() * math.sqrt(52) if len(rets) > 2 else float("nan")
        sharpe = (rets.mean() * 52 - 0.02) / vol if vol and vol == vol and vol > 0 else None
        return {"total_return_pct": round((float(s.iloc[-1]) - 1) * 100, 2),
                "max_drawdown_pct": round(dd * 100, 2),
                "sharpe": round(sharpe, 2) if sharpe and sharpe == sharpe else None}
    strategy = _quick_metrics(eq_curve)
    buyhold = _quick_metrics(bench_curve)

    return {
        "ok": True,
        "query": query,
        "pool_size": len(syms),
        "n_weeks": len(pairs),
        "predictive": {
            "ic_level_spearman": None if ic_level is None else round(ic_level, 3),
            "ic_accel_spearman": None if ic_accel is None else round(ic_accel, 3),
            "corr_level_fwd": None if corr_level is None else round(corr_level, 3),
            "corr_same_period": None if corr_same is None else round(corr_same, 3),
            "orth_ic_residual": None if orth_ic is None else round(orth_ic, 3),
        },
        "tercile_next_week_ret_pct": {
            "low_attention": None if low_ret is None else round(low_ret * 100, 2),
            "mid": None if mid_ret is None else round(mid_ret * 100, 2),
            "high_attention": None if high_ret is None else round(high_ret * 100, 2),
        },
        "strategy_attention_timing": strategy,
        "buyhold": buyhold,
        "note": ("IC>0 且同期相关不显著 → 真预测；IC≈同期相关 → 只是共振；"
                 "样本<52周，结论为初步。非投资建议。"),
    }
