"""组合分析与绩效报告：quantstats 绩效指标 + PyPortfolioOpt 组合优化。

- performance_metrics(returns)     → 关键绩效指标 dict（Sharpe/Sortino/回撤/胜率…）
- tear_sheet_html(returns, path)   → quantstats HTML 报告存盘（挂到 Obsidian）
- optimize(prices_df, method)      → 组合建议权重（max_sharpe / min_volatility / hrp）

数据纪律：样本不足 / 非数值输入直接返回 ok=False 与原因，不编造数字。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..utils.common import setup_logging

log = setup_logging("investlab.analytics")

OPT_METHODS = ("max_sharpe", "min_volatility", "hrp")


def _clean_returns(returns: pd.Series) -> pd.Series:
    r = pd.Series(returns).dropna().astype(float)
    r = r[np.isfinite(r)]
    return r


def performance_metrics(returns: pd.Series, *, rf: float = 0.02,
                        trading_days: int = 252) -> dict:
    """用 quantstats 计算核心绩效指标；样本 <20 个交易日返回 ok=False。"""
    import quantstats as qs

    r = _clean_returns(returns)
    if len(r) < 20:
        return {"ok": False, "error": f"收益样本不足({len(r)}日)，至少需要20日"}
    try:
        out = {
            "ok": True,
            "days": int(len(r)),
            "total_return_pct": round(float(qs.stats.comp(r) * 100), 2),
            "cagr_pct": round(float(qs.stats.cagr(r, rf=rf) * 100), 2),
            "sharpe": round(float(qs.stats.sharpe(r, rf=rf)), 2),
            "sortino": round(float(qs.stats.sortino(r, rf=rf)), 2),
            "volatility_pct": round(float(qs.stats.volatility(r) * 100), 2),
            "max_drawdown_pct": round(float(qs.stats.max_drawdown(r) * 100), 2),
            "calmar": round(float(qs.stats.calmar(r)), 2),
            "win_rate_pct": round(float((r > 0).mean() * 100), 1),
            "best_day_pct": round(float(r.max()) * 100, 2),
            "worst_day_pct": round(float(r.min()) * 100, 2),
            "skew": round(float(qs.stats.skew(r)), 2),
            "kurtosis": round(float(qs.stats.kurtosis(r)), 2),
            "var_95_pct": round(float(qs.stats.var(r, sigma=1.645) * 100), 2),
        }
        # drawdown_details 的列名/单位随 quantstats 版本变化，防御式定位并归一化
        try:
            dd = qs.stats.drawdown_details(r)
            if dd is not None and hasattr(dd, "empty") and not dd.empty:
                dd_cols = {c.lower(): c for c in dd.columns}
                dd_col = next((dd_cols[c] for c in
                               ("max drawdown", "max drawdown %", "drawdown") if c in dd_cols), None)
                if dd_col:
                    worst = dd.sort_values(dd_col).iloc[0]
                    raw_dd = float(worst[dd_col])
                    # 部分版本返回 -13.43（已为百分数），部分返回 -0.1343（小数）
                    dd_frac = raw_dd / 100 if abs(raw_dd) > 1 else raw_dd
                    start_col = dd_cols.get("started") or dd_cols.get("opened") or ""
                    end_col = dd_cols.get("ended") or dd_cols.get("closed") or ""
                    days = worst.get(dd_cols.get("days", ""), 0)
                    started = str(worst.get(start_col, ""))[:10] if start_col else ""
                    out["worst_dd_window"] = {
                        "start": started or "(进行中)",
                        "end": (str(worst.get(end_col, ""))[:10]
                                if end_col else "") or "(进行中)",
                        "days": int(days) if days else 0,
                        "depth_pct": round(dd_frac * 100, 2),
                    }
        except Exception as exc:  # 该窗口只是补充信息，失败不阻塞指标
            log.debug("drawdown_details 解析失败（跳过）: %s", exc)
        return out
    except Exception as exc:
        log.warning("quantstats 计算失败: %s", exc)
        return {"ok": False, "error": f"绩效计算失败: {exc}"}


def tear_sheet_html(returns: pd.Series, out_path: Path, *,
                    title: str = "InvestLab 绩效报告") -> dict:
    """生成 quantstats HTML 报告文件。依赖 matplotlib 渲染，失败返回 ok=False。"""
    import matplotlib
    matplotlib.use("Agg")  # 无 GUI 环境

    import quantstats as qs

    r = _clean_returns(returns)
    if len(r) < 20:
        return {"ok": False, "error": f"收益样本不足({len(r)}日)"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        qs.reports.html(r, output=str(out_path), title=title)
        if out_path.is_file() and out_path.stat().st_size > 500:
            return {"ok": True, "path": str(out_path),
                    "size_kb": round(out_path.stat().st_size / 1024, 1)}
        return {"ok": False, "error": "报告文件未生成"}
    except Exception as exc:
        log.warning("tear sheet 生成失败: %s", exc)
        return {"ok": False, "error": f"报告生成失败: {exc}"}


# ------------------------------------------------------------------ 组合优化


def prices_frame(candles_by_symbol: dict[str, list[dict]]) -> pd.DataFrame:
    """{symbol: candles} → 收盘价宽表（日期 × 标的，内部对齐）。"""
    series = {}
    for sym, candles in candles_by_symbol.items():
        if not candles:
            continue
        df = pd.DataFrame(candles)
        df["date"] = pd.to_datetime(df["date"])
        s = df.set_index("date")["close"].astype(float)
        series[sym] = s
    if not series:
        return pd.DataFrame()
    prices = pd.DataFrame(series).sort_index()
    # 行/列双向覆盖过滤：日期需 ≥60% 标的有报价，标的需覆盖 ≥60% 日期
    min_cover = max(2, int(len(series) * 0.6))
    prices = prices.dropna(thresh=min_cover)
    if prices.empty:
        return prices
    min_days = max(2, int(len(prices) * 0.6))
    prices = prices.dropna(axis=1, thresh=min_days)
    return prices


def optimize(prices: pd.DataFrame, *, method: str = "hrp",
             max_weight: float = 0.35, risk_free: float = 0.02) -> dict:
    """PyPortfolioOpt 建议权重。

    method: max_sharpe | min_volatility | hrp
    返回 {ok, weights:{sym: pct}, metrics:{expected_annual_return_pct, annual_volatility_pct, sharpe}(EF类) 或 {hrp_extra}}
    """
    if method not in OPT_METHODS:
        return {"ok": False, "error": f"method 须为 {OPT_METHODS}"}
    if prices is None or prices.empty or prices.shape[1] < 2:
        return {"ok": False, "error": "至少需要2只标的且有价格历史"}

    # 剔除历史过短的标的（<120 日），避免协方差失真
    valid = [c for c in prices.columns if prices[c].dropna().shape[0] >= 120]
    if len(valid) < 2:
        return {"ok": False, "error": "有效价格历史(≥120日)的标的不足2只"}
    prices = prices[valid].ffill().dropna()
    if len(prices) < 120:
        return {"ok": False, "error": f"共同历史不足({len(prices)}日<120)"}

    try:
        if method == "hrp":
            from pypfopt import HRPOpt
            from pypfopt import risk_models as rm

            returns = prices.pct_change().dropna()
            shrunk = rm.CovarianceShrinkage(prices).ledoit_wolf()
            hrp = HRPOpt(returns=returns, cov_matrix=shrunk)
            weights = hrp.optimize()
            port_ret = returns @ pd.Series(weights).reindex(returns.columns).fillna(0)
            metrics = {
                "annual_volatility_pct": round(float(port_ret.std() * np.sqrt(252)) * 100, 2),
                "method_note": "层次风险平价(HRP)+Ledoit-Wolf收缩协方差",
            }
        else:
            from pypfopt import EfficientFrontier, expected_returns, objective_functions
            from pypfopt import risk_models as rm

            mu = expected_returns.mean_historical_return(prices)
            s = rm.CovarianceShrinkage(prices).ledoit_wolf()
            ef = EfficientFrontier(mu, s, weight_bounds=(0, max_weight))
            if method == "max_sharpe":
                # L2 正则避免解过度集中于个别标的（pypfopt≥1.5 名称 L2_reg）
                ef.add_objective(objective_functions.L2_reg, gamma=0.1)
                ef.max_sharpe(risk_free_rate=risk_free)
            else:
                ef.min_volatility()
            weights = ef.clean_weights()
            ret, vol, sharpe = ef.portfolio_performance(risk_free_rate=risk_free)
            metrics = {
                "expected_annual_return_pct": round(ret * 100, 2),
                "annual_volatility_pct": round(vol * 100, 2),
                "sharpe": round(sharpe, 2),
                "method_note": "均值-方差有效前沿(L2正则+单标的≤"
                               f"{max_weight:.0%})，历史均值收益估计",
            }
    except Exception as exc:
        log.warning("组合优化失败: %s", exc)
        return {"ok": False, "error": f"优化失败: {exc}"}

    clean = {k: round(float(v), 4) for k, v in weights.items() if float(v) > 0.0005}
    total = sum(clean.values()) or 1.0
    clean = {k: round(v / total, 4) for k, v in clean.items()}
    return {"ok": True, "method": method, "weights": dict(sorted(clean.items(), key=lambda x: -x[1])),
            "metrics": metrics,
            "disclaimer": "基于历史协方差的统计建议，非投资建议"}


def rebalance_suggestions(current_weights: dict[str, float],
                          target_weights: dict[str, float],
                          *, threshold_pct: float = 3.0) -> list[dict]:
    """当前 vs 目标 → 调仓动作列表（|偏离| ≥ threshold 才提示）。"""
    out = []
    for sym in sorted(set(current_weights) | set(target_weights)):
        cur = float(current_weights.get(sym, 0.0))
        tgt = float(target_weights.get(sym, 0.0)) * 100
        diff = round(tgt - cur, 2)
        if abs(diff) < threshold_pct:
            continue
        action = "买入增持" if diff > 0 else "减持"
        if cur == 0:
            action = "新增"
        elif tgt == 0:
            action = "清仓"
        out.append({"symbol": sym, "current_pct": round(cur, 2),
                    "target_pct": round(tgt, 2), "diff_pct": diff, "action": action})
    return sorted(out, key=lambda x: -abs(x["diff_pct"]))
