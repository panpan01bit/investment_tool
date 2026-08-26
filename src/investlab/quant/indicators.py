"""技术指标：纯 numpy/pandas 实现（参考 UZI-Skill 的零三方依赖思路）。

输入统一为 candles list[dict]（见 datasources.candles），Series 为 close 序列。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd


def to_frame(candles: Sequence[dict]) -> pd.DataFrame:
    """candles → DataFrame(date index, open/high/low/close/volume)。"""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(list(candles))
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=max(2, n // 2)).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return {"dif": dif, "dea": dea, "hist": hist}


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    diff = close.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def kdj(df: pd.DataFrame, n: int = 9) -> dict:
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return {"k": k.fillna(50), "d": d.fillna(50), "j": j.fillna(50)}


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> dict:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=max(2, n // 2)).std(ddof=0)
    return {"mid": mid, "upper": mid + k * std, "lower": mid - k * std, "std": std}


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n, min_periods=1).max()
    ll = df["low"].rolling(n, min_periods=1).min()
    rng = (hh - df["close"]) / (hh - ll).replace(0, np.nan) * -100
    return rng.fillna(-50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ------------------------------------------------------------------ 绩效统计


def daily_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().dropna()


def annualized_volatility(close: pd.Series, trading_days: int = 252) -> float:
    rets = daily_returns(close)
    if len(rets) < 5:
        return float("nan")
    return float(rets.std(ddof=0) * math.sqrt(trading_days))


def max_drawdown(close: pd.Series) -> float:
    if len(close) < 2:
        return float("nan")
    cummax = close.cummax()
    dd = (close / cummax - 1).min()
    return float(dd)


def sharpe_ratio(close: pd.Series, risk_free_annual: float = 0.02,
                 trading_days: int = 252) -> float:
    rets = daily_returns(close)
    if len(rets) < 10 or rets.std(ddof=0) == 0:
        return float("nan")
    excess_mean = rets.mean() * trading_days - risk_free_annual
    return float(excess_mean / (rets.std(ddof=0) * math.sqrt(trading_days)))


def beta(close: pd.Series, bench_close: pd.Series) -> float:
    joined = pd.concat([daily_returns(close), daily_returns(bench_close)],
                       axis=1, join="inner").dropna()
    if len(joined) < 20:
        return float("nan")
    var_b = float(joined.iloc[:, 1].var(ddof=0))
    if var_b == 0:
        return float("nan")
    cov = float(joined.cov().iloc[0, 1])
    return cov / var_b


def snapshot_indicators(candles: list[dict]) -> dict:
    """一把梭：把信号引擎要用的指标全算好返回标量。"""
    df = to_frame(candles)
    if df.empty or len(df) < 30:
        return {}
    close = df["close"]
    m = macd(close)
    k = kdj(df)
    b = bollinger(close)
    last = close.iloc[-1]
    hi52 = df["high"].tail(min(len(df), 250)).max()
    lo52 = df["low"].tail(min(len(df), 250)).min()
    ma5, ma10, ma20 = sma(close, 5).iloc[-1], sma(close, 10).iloc[-1], sma(close, 20).iloc[-1]
    ma60 = sma(close, 60).iloc[-1]
    ma200_series = sma(close, 200)
    ma200 = ma200_series.iloc[-1]
    vol_mean_20 = df["volume"].tail(21).head(20).mean() or float("nan")
    vol_last = df["volume"].iloc[-1]
    return {
        "last_date": str(df.index[-1].date()),
        "close": round(float(last), 4),
        "ma5": _r(ma5), "ma10": _r(ma10), "ma20": _r(ma20),
        "ma60": _r(ma60), "ma200": _r(ma200),
        "ma_bull_alignment": bool(
            ma5 > ma10 > ma20 if all(map(lambda x: x == x and x is not None and not pd.isna(x), [ma5, ma10, ma20])) else False
        ),
        "macd_dif": _r(m["dif"].iloc[-1]),
        "macd_dea": _r(m["dea"].iloc[-1]),
        "macd_hist": _r(m["hist"].iloc[-1]),
        "macd_golden_cross_recent": _golden_cross_recent(m["dif"], m["dea"]),
        "rsi14": _r(rsi(close).iloc[-1], 1),
        "kdj_k": _r(k["k"].iloc[-1], 1), "kdj_j": _r(k["j"].iloc[-1], 1),
        "boll_pos": _boll_position(last, b),
        "obv_slope20": _slope(obv(df).tail(21)),
        "wr14": _r(williams_r(df).iloc[-1], 1),
        "atr14_ratio": _ratio(atr(df).iloc[-1], last),
        "ann_vol": _r(annualized_volatility(close), 3),
        "max_dd_1y": _r(max_drawdown(close.tail(250)), 4),
        "dist_52w_high_pct": None if pd.isna(hi52) else round((last / hi52 - 1) * 100, 1),
        "dist_52w_low_pct": None if pd.isna(lo52) else round((last / lo52 - 1) * 100, 1),
        "weinstein_stage_guess": _weinstein_stage(close, ma200_series),
        "volume_vs_ma20": _ratio(vol_last, vol_mean_20),
    }


def _golden_cross_recent(dif: pd.Series, dea: pd.Series, lookback: int = 5) -> bool:
    """近 lookback 根内出现 DIF 上穿 DEA。"""
    a = (dif - dea).tail(lookback + 1)
    return bool((a.iloc[:-1] <= 0).any() and a.iloc[-1] > 0)


def _boll_position(price: float, b: dict):
    upper, lower = b["upper"].iloc[-1], b["lower"].iloc[-1]
    try:
        span = upper - lower
        if pd.isna(span) or span == 0:
            return None
        return round(float((price - lower) / span), 2)
    except TypeError:
        return None


def _slope(series: pd.Series) -> float | None:
    s = series.dropna()
    if len(s) < 5:
        return None
    x = np.arange(len(s))
    coef = np.polyfit(x, s.to_numpy(dtype=float), 1)[0]
    denom = abs(float(s.iloc[0])) or 1.0
    return round(coef / denom, 4)


def _weinstein_stage(close: pd.Series, ma200: pd.Series) -> str | None:
    """韦恩斯坦四阶段粗判。"""
    valid = ma200.dropna()
    if len(valid) < 60 or len(close) < 220:
        return None
    slope = (valid.iloc[-1] / valid.iloc[-40] - 1) if len(valid) >= 40 else 0
    price_above = close.iloc[-1] > valid.iloc[-1]
    if slope > 0.01 and price_above:
        return "第二阶段·上升"
    if slope < -0.01 and not price_above:
        return "第四阶段·下降"
    if abs(slope) <= 0.01:
        return "第一阶段·筑底" if not price_above else "第三阶段·做头"
    return None


def _ratio(a, b):
    try:
        fa, fb = float(a), float(b)
        if fb != fb or fb == 0:
            return None
        return round(fa / fb, 2)
    except (TypeError, ValueError):
        return None


def _r(v, ndigits: int = 4):
    try:
        f = float(v)
        return None if f != f else round(f, ndigits)
    except (TypeError, ValueError):
        return None
