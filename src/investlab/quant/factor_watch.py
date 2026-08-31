"""市场风格仪表（factor watch）：用免费指数数据实证"当前周期哪种风格有效"。

参考方法论：头部量化选股私募（明汯/九坤/鸣石/世纪前沿等）的超额主要来自
量价类因子（短动量/反转/波动/换手）+ 机器学习合成；本模块用指数代理把
"风格周期"量化出来，指导我们信号引擎的权重适配。

全部数据来自 akshare 免费指数日线，无密钥。输出结构化 dict + Obsidian 报告。
"""

from __future__ import annotations

import math
from datetime import datetime

from ..utils.common import cache_get, cache_put, setup_logging, today_str

log = setup_logging("investlab.factors")

FACTORS_TTL_S = 6 * 3600.0

# 风格代理指数（东财 secid 为主链，腾讯 ifzq 为备用链）
_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
INDEX_CANDIDATES = {
    "hs300": ["1.000300"],             # 沪深300（大盘）
    "zz500": ["1.000905"],             # 中证500（中盘）
    "zz1000": ["1.000852"],            # 中证1000（小盘）
    "zz2000": ["2.932000", "0.399303"],  # 中证2000/国证2000（微盘代理）
    "total": ["1.000985"],             # 中证全指（市场基准/波动）
}
# 腾讯备用代码（与 INDEX_CANDIDATES 键对应）
TENCENT_FALLBACK = {
    "hs300": "sh000300",
    "zz500": "sh000905",
    "zz1000": "sh000852",
    "zz2000": "sz399303",
    "total": "sh000985",
}


def _fetch_index(secid: str, days: int = 300) -> list[dict]:
    from ..netguard import http_get_json

    data = http_get_json(
        _EM_KLINE_URL,
        params={
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": "101", "fqt": "1",
            "lmt": str(max(days, 60)), "end": "20500101",
        },
        timeout=10, retries=2,
    )
    klines = ((data or {}).get("data") or {}).get("klines") or []
    rows = []
    for line in klines:
        p = str(line).split(",")
        if len(p) < 7:
            continue
        try:
            rows.append({
                "date": p[0],
                "close": float(p[2]),
                "volume": float(p[5]),
                "amount": float(p[6]),
            })
        except ValueError:
            continue
    return rows[-days:]


def _tencent_index_kline(code: str, days: int = 300) -> list[dict]:
    """腾讯 ifzq 日K备用链（指数可用；无成交额字段，用成交量代替）。"""
    from ..netguard import http_get_json

    n = min(days, 320)
    payload = http_get_json(
        _TENCENT_KLINE_URL,
        params={"param": f"{code},day,,,{n},qfq"},
        timeout=10, retries=2,
    )
    node = (payload or {}).get("data", {}).get(code) or {}
    klines = node.get("qfqday") or node.get("day") or []
    rows = []
    for row in klines:
        try:
            rows.append({
                "date": str(row[0]),
                "close": float(row[2]),
                "volume": float(row[5]) if len(row) > 5 else 0.0,
                "amount": float(row[5]) if len(row) > 5 else 0.0,  # 比值场景等价
            })
        except (IndexError, TypeError, ValueError):
            continue
    return rows[-days:]


def _index_series(key: str, days: int = 300) -> list[dict]:
    import time

    # 结果级 TTL 缓存（1小时）：避免轮动回测等场景反复拉同一指数触发限流
    cached = cache_get("index_series_v1", [key, days], 3600.0)
    if cached:
        return cached

    # 主链：东财 push2his（含成交额）
    for secid in INDEX_CANDIDATES.get(key, []):
        for attempt in range(2):
            try:
                rows = _fetch_index(secid, days)
                if len(rows) >= 60:
                    cache_put("index_series_v1", [key, days], rows, ttl_s=3600.0)
                    return rows
            except Exception as exc:
                log.debug("指数 %s(%s) 第%d次拉取失败: %s", key, secid, attempt + 1, exc)
            time.sleep(1.0 + attempt)
    # 备用链：腾讯 ifzq
    code = TENCENT_FALLBACK.get(key)
    if code:
        for attempt in range(3):
            try:
                rows = _tencent_index_kline(code, days)
                if len(rows) >= 60:
                    log.debug("指数 %s 使用腾讯备用链", key)
                    cache_put("index_series_v1", [key, days], rows, ttl_s=3600.0)
                    return rows
            except Exception as exc:
                log.debug("腾讯指数 %s 第%d次失败: %s", code, attempt + 1, exc)
            time.sleep(1.5 + attempt)
    return []


def _ret(rows: list[dict], n: int) -> float | None:
    if len(rows) < n + 1:
        return None
    return rows[-1]["close"] / rows[-1 - n]["close"] - 1


def _realized_vol(rows: list[dict], n: int = 20) -> float | None:
    if len(rows) < n + 1:
        return None
    rets = [rows[i]["close"] / rows[i - 1]["close"] - 1 for i in range(len(rows) - n, len(rows))]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252)


def _autocorr(rows: list[dict], n: int = 60, lag: int = 1) -> float | None:
    """日收益自相关：<0 偏反转市，>0 偏动量市。"""
    if len(rows) < n + 1:
        return None
    rets = [rows[i]["close"] / rows[i - 1]["close"] - 1 for i in range(len(rows) - n, len(rows))]
    m = sum(rets) / len(rets)
    num = sum((rets[i] - m) * (rets[i + lag] - m) for i in range(len(rets) - lag))
    den = sum((r - m) ** 2 for r in rets)
    return num / den if den else None


def _amount_ratio(rows: list[dict]) -> float | None:
    if len(rows) < 60:
        return None
    recent = sum(r["amount"] for r in rows[-20:]) / 20
    prior = sum(r["amount"] for r in rows[-60:-20]) / 40
    return recent / prior if prior else None


def factor_watch(*, use_cache: bool = True, days: int = 300) -> dict:
    """当前周期的风格因子实证。返回 {styles:{...}, regime, suggestions, ts}。"""
    key = ["factor_watch_v1", days]
    if use_cache:
        cached = cache_get("factor_watch_v1", key, FACTORS_TTL_S)
        if cached:
            return cached

    series = {k: _index_series(k, days) for k in INDEX_CANDIDATES}
    styles: dict = {}
    suggestions: list[str] = []

    # ---- 规模因子：小盘/微盘相对大盘的 20/60 日强弱
    small_r20 = _ret(series["zz1000"], 20)
    big_r20 = _ret(series["hs300"], 20)
    micro_r60 = _ret(series["zz2000"], 60)
    big_r60 = _ret(series["hs300"], 60)
    if small_r20 is not None and big_r20 is not None:
        spread20 = small_r20 - big_r20
        styles["size_20d"] = {
            "name": "规模因子（小盘-大盘，20日）",
            "value": round(spread20, 4), "pct": round(spread20 * 100, 2),
            "verdict": "小盘占优" if spread20 > 0 else "大盘占优",
        }
    if micro_r60 is not None and big_r60 is not None:
        spread60 = micro_r60 - big_r60
        styles["size_60d_micro"] = {
            "name": "微盘-大盘（60日）",
            "value": round(spread60, 4), "pct": round(spread60 * 100, 2),
            "verdict": "微盘强势" if spread60 > 0 else "微盘弱势",
        }

    # ---- 动量/反转：全指 60 日动量 + 日收益自相关判定市场机制
    total = series["total"]
    mom60 = _ret(total, 60)
    ac = _autocorr(total)
    if mom60 is not None:
        styles["momentum_60d"] = {
            "name": "市场动量（全指60日）",
            "value": round(mom60, 4), "pct": round(mom60 * 100, 2),
            "verdict": "动量上行" if mom60 > 0.03 else ("动量下行" if mom60 < -0.03 else "无趋势/震荡"),
        }
    if ac is not None:
        regime = "反转市（均值回归主导）" if ac < -0.05 else ("动量市（趋势延续主导）" if ac > 0.05 else "中性")
        styles["return_autocorr"] = {
            "name": "日收益自相关（60日, lag1）",
            "value": round(ac, 3),
            "verdict": regime,
        }
        if ac < -0.05:
            suggestions.append("反转市：信号引擎中'超卖回升/KDJ超卖'类规则可信度更高，追高动量规则降权")
        elif ac > 0.05:
            suggestions.append("动量市：'均线多头/韦恩斯坦二阶段/突破'类规则加权，反转抄底慎用")

    # ---- 波动率：20日实现波动及其在一年中的分位
    vol = _realized_vol(total)
    if vol is not None and len(total) >= 240:
        hist_vols = [
            _realized_vol(total[: len(total) - i]) for i in range(0, 220, 5)
        ]
        hist_vols = [v for v in hist_vols if v is not None]
        pct_rank = sum(1 for v in hist_vols if v <= vol) / len(hist_vols)
        styles["volatility_20d"] = {
            "name": "20日实现波动率",
            "value": round(vol, 3), "pct": round(vol * 100, 2),
            "percentile_1y": round(pct_rank * 100, 0),
            "verdict": "高波动" if pct_rank > 0.7 else ("低波动" if pct_rank < 0.3 else "中波动"),
        }
        if pct_rank > 0.7:
            suggestions.append("高波动期：降低单标的集中度，回测参数取更短持有期更稳")

    # ---- 量能：全指成交额 20日均量 vs 60日均量
    amt = _amount_ratio(total)
    if amt is not None:
        styles["volume_trend"] = {
            "name": "成交额趋势（20日/60日均量比）",
            "value": round(amt, 2),
            "verdict": "放量" if amt > 1.15 else ("缩量" if amt < 0.85 else "平量"),
        }
        if amt < 0.85:
            suggestions.append("缩量期：小市值流动性溢价收缩，微盘暴露需谨慎")

    regime = _regime_summary(styles)
    out = {
        "date": today_str(),
        "styles": styles,
        "regime": regime,
        "suggestions": suggestions,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    cache_put("factor_watch_v1", key, out, ttl_s=FACTORS_TTL_S)
    return out


def _regime_summary(styles: dict) -> str:
    bits = []
    if "size_20d" in styles:
        bits.append(styles["size_20d"]["verdict"])
    if "momentum_60d" in styles:
        bits.append(styles["momentum_60d"]["verdict"])
    if "return_autocorr" in styles:
        bits.append(styles["return_autocorr"]["verdict"])
    if "volatility_20d" in styles:
        bits.append(styles["volatility_20d"]["verdict"])
    return " · ".join(bits) if bits else "数据不足"
