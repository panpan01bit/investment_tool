"""轻量向量化回测引擎（参考 backtesting.py/vectorbt 思路的最小子集）。

支持策略：
- sma_cross       快慢均线金叉买、死叉卖
- rsi_reversion   RSI<n 买入、RSI>m 卖出
- breakout_20     创 N 日新高买入、跌破 MA 卖出

输出：指标 dict（CAGR/Sharpe/最大回撤/胜率/交易次数）+ 净值曲线，供前端画图。
免责：教学与研究用途，非实盘绩效承诺。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..datasources.candles import get_candles
from ..utils.common import setup_logging
from . import indicators as ta

log = setup_logging("investlab.backtest")

STRATEGIES = ("sma_cross", "rsi_reversion", "breakout_20")


def run_backtest(
    symbol: str,
    *,
    strategy: str = "sma_cross",
    days: int = 500,
    fast: int = 20,
    slow: int = 60,
    rsi_buy: float = 30.0,
    rsi_sell: float = 70.0,
    fee_bps: float = 5.0,        # 单边费用（万分之一）
    slippage_bps: float = 5.0,
) -> dict:
    s = symbol
    candles = get_candles(s, days=days)
    df = ta.to_frame(candles)
    if len(df) < max(slow + 20, 80):
        return {"symbol": s, "ok": False,
                "error": f"数据不足({len(df)}根)，无法回测策略 {strategy}"}

    close = df["close"]
    pos = _positions(close, strategy, fast, slow, rsi_buy, rsi_sell)

    rets = close.pct_change().fillna(0)
    turnover = pos.diff().abs().fillna(0)
    cost = turnover * (fee_bps + slippage_bps) / 10000
    strat_rets = pos.shift(1).fillna(0) * rets - cost
    equity = (1 + strat_rets).cumprod()
    bench_equity = (1 + rets).cumprod()

    trades = _trade_list(pos, close)
    win_rate = round(
        100 * sum(1 for t in trades if t["pnl"] > 0) / len(trades), 1
    ) if trades else None

    exposure = float(pos.mean()) if len(pos) else 0.0
    metrics = {
        "total_return_pct": round((float(equity.iloc[-1]) - 1) * 100, 2),
        "bench_return_pct": round((float(bench_equity.iloc[-1]) - 1) * 100, 2),
        "cagr_pct": _cagr(equity),
        "sharpe": ta.sharpe_ratio(equity, risk_free_annual=0.02),
        "max_drawdown_pct": round(ta.max_drawdown(equity) * 100, 2),
        "win_rate_pct": win_rate,
        "trades": len(trades),
        "exposure_pct": round(exposure * 100, 1),
    }
    curve = [
        {"date": str(d.date()), "strategy": round(float(v), 4),
         "benchmark": round(float(b), 4)}
        for d, v, b in zip(equity.index, equity.values, bench_equity.values, strict=False)
    ]
    return {
        "symbol": s,
        "strategy": strategy,
        "params": {"fast": fast, "slow": slow, "rsi_buy": rsi_buy,
                   "rsi_sell": rsi_sell, "fee_bps": fee_bps,
                   "slippage_bps": slippage_bps},
        "ok": True,
        "metrics": metrics,
        "trade_list": trades[-30:],
        "curve": curve,
    }


def _positions(close: pd.Series, strategy: str, fast, slow, rsi_buy, rsi_sell) -> pd.Series:
    if strategy == "sma_cross":
        sig = (ta.sma(close, fast) > ta.sma(close, slow)).astype(int)
    elif strategy == "rsi_reversion":
        r = ta.rsi(close, 14)
        raw = pd.Series(np.nan, index=close.index)
        raw[r < rsi_buy] = 1
        raw[r > rsi_sell] = 0
        sig = raw.ffill().fillna(0).astype(int)
    elif strategy == "breakout_20":
        hh = close.rolling(20, min_periods=10).max()
        ma = ta.sma(close, 50)
        raw = pd.Series(np.nan, index=close.index)
        raw[close >= hh.shift(1)] = 1
        raw[close < ma] = 0
        sig = raw.ffill().fillna(0).astype(int)
    else:
        raise ValueError(f"未知策略 {strategy}；可选 {STRATEGIES}")
    return sig


def _trade_list(pos: pd.Series, close: pd.Series) -> list[dict]:
    """配对进出点，记录区间收益（含往返成本近似）。"""
    changes = pos.diff()
    entries = list(changes[changes == 1].index)
    exits = [x for x in changes[changes == -1].index]
    cost_rt = (5 + 5) / 10000 * 2  # 展示用近似往返成本
    out = []
    for e_dt in entries:
        exits_after = [x for x in exits if x >= e_dt]
        if not exits_after:
            # 持仓至今：以最后收盘价作为虚拟出场
            exit_price = float(close.iloc[-1])
            exit_date = str(close.index[-1].date())
        else:
            x_dt = exits_after[0]
            exit_price = float(close.loc[x_dt])
            exit_date = str(x_dt.date())
        try:
            entry_price = float(close.loc[e_dt])
            pnl = exit_price / entry_price - 1 - cost_rt
        except (KeyError, ZeroDivisionError):
            continue
        out.append({"entry": str(e_dt.date()), "exit": exit_date, "pnl": round(pnl, 4)})
        if len(out) >= 200:
            break
    return out


def _cagr(equity: pd.Series) -> float:
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    total = float(equity.iloc[-1])
    if total <= 0:
        return -100.0
    years = days / 365
    return (
        round(((total ** (1 / years)) - 1) * 100, 2)
        if years > 0.3
        else round((total - 1) * 100, 2)
    )
