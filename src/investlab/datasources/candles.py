"""K线历史（日线）多路回退链。

返回统一 list[dict]：{date, open, high, low, close, volume}（升序）。
回退顺序：eastmoney push2his 直连 → 腾讯 → akshare。全部出站请求经 netguard 校验。
缓存 TTL：日线 2 小时。
"""

from __future__ import annotations

from ..netguard import http_get_json
from ..utils.common import cache_get, cache_put, setup_logging
from . import symbols as sym
from .quotes import _safe_call

log = setup_logging("investlab.candles")

CANDLES_TTL_S = 2 * 3600.0


def get_candles(symbol: str, *, days: int = 250, use_cache: bool = True) -> list[dict]:
    """取最近 days 个交易日的日线 OHLCV。所有源失败返回 []。"""
    s = sym.normalize(symbol)
    if not s:
        return []
    key = [s, days]
    if use_cache:
        cached = cache_get("candles_v1", key, CANDLES_TTL_S)
        if cached:
            return cached

    chain = [_candles_eastmoney, _candles_tencent, _candles_akshare]
    for fn in chain:
        rows = _safe_call(fn, s, days) or []
        if len(rows) >= 10:
            rows.sort(key=lambda r: r["date"])
            cache_put("candles_v1", key, rows, ttl_s=CANDLES_TTL_S)
            return rows
    log.warning("K线获取失败（所有源）: %s", s)
    return []


def latest_close(symbol: str, *, use_cache: bool = True):
    rows = get_candles(symbol, days=5, use_cache=use_cache)
    return rows[-1]["close"] if rows else None


# ------------------------------------------------------------------ 东财 push2his

_EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _candles_eastmoney(symbol: str, days: int) -> list[dict]:
    secid = sym.eastmoney_secid(symbol)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",
        "fqt": "1",
        "lmt": str(max(days, 30)),
        "end": "20500101",
    }
    data = http_get_json(_EM_KLINE_URL, params=params, timeout=10)
    klines = ((data or {}).get("data") or {}).get("klines") or []
    out = []
    for line in klines:
        p = str(line).split(",")
        if len(p) < 7:
            continue
        try:
            out.append(
                {
                    "date": p[0],
                    "open": float(p[1]),
                    "close": float(p[2]),
                    "high": float(p[3]),
                    "low": float(p[4]),
                    "volume": float(p[5]),
                }
            )
        except ValueError:
            continue
    return out[-days:]


# ------------------------------------------------------------------ 腾讯 ifzq

_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _candles_tencent(symbol: str, days: int) -> list[dict]:
    code = sym.tencent_symbol(symbol)
    n = min(days, 320)
    params = {"param": f"{code},day,,,{n},qfq"}
    payload = http_get_json(_TENCENT_KLINE_URL, params=params, timeout=10)
    node = (payload or {}).get("data", {}).get(code) or {}
    klines = node.get("qfqday") or node.get("day") or []
    out = []
    for row in klines:
        try:
            out.append(
                {
                    "date": str(row[0]),
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]) if len(row) > 5 else 0.0,
                }
            )
        except (IndexError, TypeError, ValueError):
            continue
    return out[-days:]


# ------------------------------------------------------------------ akshare


def _candles_akshare(symbol: str, days: int) -> list[dict]:
    try:
        import akshare as ak
    except ImportError:
        log.debug("akshare 未安装，跳过")
        return []
    s = sym.normalize(symbol)
    mkt = sym.market_of(s)
    try:
        if mkt == "CH":
            df = ak.stock_zh_a_hist(symbol=s[:6], period="daily", adjust="qfq")
        elif mkt == "HK":
            df = ak.stock_hk_hist(symbol=s[:5], period="daily", adjust="qfq")
        else:
            df = ak.stock_us_hist(
                symbol=sym.yfinance_symbol(s), period="daily", adjust="qfq"
            )
    except Exception as exc:
        log.debug("akshare 日线失败 %s: %s", s, exc)
        return []
    return _df_to_candles(df)[-days:]


def _df_to_candles(df) -> list[dict]:
    """akshare 中文列 DataFrame → 统一 candles 格式。"""
    if df is None or df.empty:
        return []
    rename = {}
    for col in df.columns:
        c = str(col)
        if c == "日期":
            rename[c] = "date"
        elif c == "开盘":
            rename[c] = "open"
        elif c == "收盘":
            rename[c] = "close"
        elif c == "最高":
            rename[c] = "high"
        elif c == "最低":
            rename[c] = "low"
        elif c.startswith("成交量"):
            rename[c] = "volume"
    df = df.rename(columns=rename)
    need = {"date", "open", "high", "low", "close"}
    if not need.issubset(df.columns):
        return []
    out = []
    for _, r in df.iterrows():
        try:
            out.append(
                {
                    "date": str(r["date"])[:10],
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume") or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    return out
