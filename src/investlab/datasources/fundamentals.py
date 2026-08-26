"""基本面快照：财务指标 + 估值，akshare（免费）优先，tushare（可选 token）补充。

输出统一 dict：
  {symbol, name, pe, pb, ps, roe, revenue_yoy, profit_yoy,
   gross_margin, net_margin, debt_ratio, market_cap_yi, source}
缺失字段保持 None —— 数据缺口不编造。
"""

from __future__ import annotations

from ..utils.common import cache_get, cache_put, setup_logging
from . import symbols as sym
from .quotes import _safe_call

log = setup_logging("investlab.fundamentals")

FUNDAMENTALS_TTL_S = 12 * 3600.0  # 基本面数据日频即可


def get_fundamentals(symbol: str, *, use_cache: bool = True) -> dict:
    s = sym.normalize(symbol)
    base = _empty(s)
    if not s:
        return base
    if use_cache:
        cached = cache_get("fundamentals_v1", [s], FUNDAMENTALS_TTL_S)
        if cached:
            return cached

    result = _safe_call(_fund_akshare_spot, s) or {}
    if not result.get("pe") and get_settings_token():
        tushare_result = _safe_call(_fund_tushare, s) or {}
        result = {**tushare_result, **{k: v for k, v in result.items() if v is not None}}
    result = {**base, **result}
    if any(result.get(k) is not None for k in ("pe", "pb", "roe", "revenue_yoy")):
        cache_put("fundamentals_v1", [s], result, ttl_s=FUNDAMENTALS_TTL_S)
    return result


def get_settings_token() -> str:
    from ..config import get_settings

    return get_settings().tushare_token


def _empty(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "name": "",
        "currency": {"CH": "CNY", "HK": "HKD", "US": "USD"}[sym.market_of(symbol)]
        if symbol
        else "CNY",
        "pe": None,
        "pb": None,
        "ps": None,
        "roe": None,
        "revenue_yoy": None,
        "profit_yoy": None,
        "gross_margin": None,
        "net_margin": None,
        "debt_ratio": None,
        "market_cap_yi": None,
        "source": None,
    }


# ------------------------------------------------------------------ akshare 现货估值


def _fund_akshare_spot(symbol: str) -> dict | None:
    try:
        import akshare as ak
    except ImportError:
        return None
    s = sym.normalize(symbol)
    if sym.market_of(s) != "CH":
        return _fund_akshare_non_cn(s, ak)
    df = ak.stock_a_indicator_lg(symbol=s[:6])  # 乐咕乐股：PE/PB/PS 日度指标
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    name = ""
    try:
        info = ak.stock_individual_info_em(symbol=s[:6])
        row = info[info["item"] == "股票简称"]
        name = str(row["value"].iloc[0]) if not row.empty else ""
    except Exception:
        pass
    out = {
        "name": name,
        "pe": _f(last.get("pe_ttm")),
        "pb": _f(last.get("pb")),
        "ps": _f(last.get("ps_ttm")),
        # 总市值（亿）
        "source": "akshare",
    }
    out["roe"] = None  # 单独拉财务指标，避免多接口串联超时；留待 deep 分析补齐
    return out


def _fund_akshare_non_cn(symbol: str, ak) -> dict | None:
    """港股/美股：akshare 相关接口不稳定，直接放弃（前端可显示缺口）。"""
    del ak, symbol
    return None


def _fund_tushare(symbol: str) -> dict | None:
    try:
        import tushare as ts
    except ImportError:
        return None
    from ..config import get_settings

    token = get_settings().tushare_token
    if not token:
        return None
    s = sym.normalize(symbol)
    if sym.market_of(s) != "CH":
        return None
    try:
        pro = ts.pro_api(token)
        ts_code = f"{s[:6]}.SH" if s.endswith(".SS") else f"{s[:6]}.SZ"
        fin = pro.fina_indicator(ts_code=ts_code).head(1)
        if fin.empty:
            return None
        r = fin.iloc[0]
        return {
            "roe": _f(r.get("roe")),
            "gross_margin": _mul100(r.get("grossprofit_margin")),
            "net_margin": _mul100(r.get("netprofit_margin")),
            "debt_ratio": _mul100(r.get("debt_to_assets")),
            "revenue_yoy": _mul100(r.get("or_yoy")),
            "profit_yoy": _mul100(r.get("netprofit_yoy")),
            "source": "tushare",
        }
    except Exception as exc:
        log.debug("tushare 基本面失败 %s: %s", s, exc)
        return None


def _f(v):
    try:
        f = float(v)
        return None if f != f else round(f, 4)  # NaN 过滤
    except (TypeError, ValueError):
        return None


def _mul100(v):
    f = _f(v)
    return None if f is None else round(f * 100, 2)
