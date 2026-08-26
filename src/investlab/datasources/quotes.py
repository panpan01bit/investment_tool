"""实时行情：东财 push2 → 腾讯 qt.gtimg.cn → akshare 现货，逐级回退。

统一返回 dict：
  {symbol, name, price, prev_close, change_pct, currency, source, ts}
"""

from __future__ import annotations

import re

from ..config import get_settings
from ..utils.common import cache_get, cache_put, now_cn, setup_logging
from . import symbols as sym

log = setup_logging("investlab.quotes")

QUOTES_TTL_S = 60.0


def _empty(symbol: str, source: str = "") -> dict:
    return {
        "symbol": sym.normalize(symbol),
        "name": "",
        "price": None,
        "prev_close": None,
        "change_pct": None,
        "currency": _currency_of(symbol),
        "source": source,
        "ts": now_cn().isoformat(timespec="seconds"),
    }


def _currency_of(symbol: str) -> str:
    m = sym.market_of(symbol)
    return {"CH": "CNY", "HK": "HKD", "US": "USD"}[m]


# ------------------------------------------------------------------ 东财 push2

_P2_URL = "https://push2.eastmoney.com/api/qt/stock/get"


def quote_eastmoney(symbol: str) -> dict | None:
    secid = sym.eastmoney_secid(symbol)
    fields = "43,47,48,58,60,107,116,170"
    data = _p2_get(secid, fields)
    if not data or not data.get("data"):
        return None
    d = data["data"]
    out = _empty(symbol, "eastmoney")
    out.update(
        name=d.get("58") or "",
        price=_maybe_float(d.get("43")),
        prev_close=_maybe_float(d.get("60")),
        change_pct=_maybe_scaled_pct(d.get("170")),
        currency=_currency_of(symbol),
    )
    if out["price"] is None:
        return None
    return out


def _p2_get(secid: str, fields: str):
    from ..netguard import http_get_json

    url = f"{_P2_URL}?secid={secid}&fields={fields}&invt=2&fltt=1"
    try:
        resp = http_get_json(url, timeout=8)
        if resp and isinstance(resp.get("data"), dict):
            return resp
    except Exception:
        pass
    return None


def _maybe_float(v):
    try:
        f = float(v)
        return None if f <= 0 else f
    except (TypeError, ValueError):
        return None


def _maybe_scaled_pct(v):
    """涨跌幅字段：fltt=1 时通常已是 1.23 形式；整数型 123 表示 1.23%。"""
    try:
        f = float(v)
        return round(f if abs(f) < 30 else f / 100.0, 2)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 腾讯 qt.gtimg.cn

_TENCENT_URL = "https://qt.gtimg.cn/q="


def quote_tencent(symbol: str) -> dict | None:
    code = sym.tencent_symbol(symbol)
    from ..netguard import http_get

    try:
        resp = http_get(_TENCENT_URL + code, timeout=8)
        resp.raise_for_status()
    except Exception:
        return None
    text = resp.content.decode("gbk", errors="ignore")
    m = re.search(r'"([^"]+)"', text)
    if not m:
        return None
    parts = m.group(1).split("~")
    if len(parts) < 6:
        return None
    out = _empty(symbol, "tencent")
    out.update(
        name=parts[1] or "",
        price=_to_f(parts[3]),
        prev_close=_to_f(parts[4]),
        change_pct=_to_f(parts[32]) if len(parts) > 32 else None,
    )
    if out["price"] is None:
        return None
    return out


def _to_f(s: str):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ akshare

def quote_akshare(symbol: str) -> dict | None:
    try:
        import akshare as ak
    except ImportError:
        log.debug("akshare 未安装，跳过该回退")
        return None
    s = sym.normalize(symbol)
    try:
        if s.endswith((".SS", ".SZ")):
            df = ak.stock_individual_info_em(symbol=s[:6])
            name = ""
            for _, row in df.iterrows():
                if row.iloc[0] == "股票简称":
                    name = str(row.iloc[1])
            spot = ak.stock_bid_ask_em(symbol=s[:6])
            latest = float(spot[spot["item"] == "最新"]["value"].iloc[0])
            prev = float(spot[spot["item"] == "昨收"]["value"].iloc[0])
            pct = (latest / prev - 1) * 100 if prev else None
            out = _empty(symbol, "akshare")
            out.update(name=name, price=latest, prev_close=prev,
                       change_pct=None if pct is None else round(pct, 2))
            return out
    except Exception as exc:
        log.debug("akshare 行情失败 %s: %s", symbol, exc)
    return None


# ------------------------------------------------------------------ 对外入口


def get_quote(symbol: str, *, use_cache: bool = True) -> dict:
    s = sym.normalize(symbol)
    if not s:
        return _empty(symbol)
    if use_cache:
        cached = cache_get("quote_v1", [s], QUOTES_TTL_S)
        if cached and cached.get("price"):
            return {**cached, "cached": True}

    # 按配置决定主源顺序，默认 eastmoney → tencent → akshare
    primary_first = get_settings().quotes_primary
    chain = [quote_eastmoney, quote_tencent, quote_akshare]
    if primary_first == "tencent":
        chain = [quote_tencent, quote_eastmoney, quote_akshare]

    for fn in chain:
        result = _safe_call(fn, s)
        if result:
            cache_put("quote_v1", [s], result, ttl_s=QUOTES_TTL_S)
            return result
    return _empty(s, "none")


def _safe_call(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # 单源异常不影响回退链
        log.debug("%s 失败: %s", getattr(fn, "__name__", fn), exc)
        return None


def get_quotes(list_of_symbols: list[str], *, use_cache: bool = True) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in list_of_symbols:
        ns = sym.normalize(s)
        if ns:
            out[ns] = get_quote(ns, use_cache=use_cache)
    return out
