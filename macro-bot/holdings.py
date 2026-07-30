"""holdings.py — 读取新版持仓 Excel（支持多模板）。

被 bot.py 调用。
"""

import os
from typing import Any, Dict, List, Optional, Set

import pandas as pd


# 字段列名候选（按优先级）
COL_CANDIDATES: Dict[str, List[str]] = {
    "ticker": ["Ticker"],
    "exchange": ["Exchange"],
    "short_name": ["ShortName", "Short Name", "Name", "SHORT_NAME"],
    "category": ["Category", "类别"],
    "position_size": ["PositionSize", "Position Size", "USD mkt cap", "Net in $", "持仓量"],
    "currency": ["Currency", "EQY_FUND_CRNCY", "Crncy"],
    "cost": ["CostPrice", "Cost Price", "Cost", "cost_price", "成本价"],
    "target": ["TargetPrice", "Target Price", "Target", "目标价"],
    "strategy": ["Strategy", "Thesis", "Key Logic", "关键逻辑"],
    "catalyst": ["Catalyst"],
    "risk": ["Risk", "Downside"],
    "conviction": ["Conviction"],
    "run": ["Run", "Analyze", "Active", "重点分析"],
    "notes": ["Notes", "Note"],
}


# 类别映射：中文/英文 → 数字 category_id
CATEGORY_MAP: Dict[str, int] = {
    "核心持仓": 1,
    "core": 1,
    "成长型": 2,
    "growth": 2,
    "价值型": 3,
    "value": 3,
    "对冲做空": 4,
    "对冲": 4,
    "short": 4,
    "hedge": 4,
    "watch": 5,
    "watchlist": 5,
    "重点关注": 5,
}


# 交易所后缀映射（如 .SZ, .SS, .HK, .US, .DE, .T, .PA, .SW, .GR）
EXCHANGE_SUFFIX_MAP: Dict[str, str] = {
    "ss": "CH",    # 上交所
    "sh": "CH",    # 上海
    "sz": "CH",    # 深交所
    "hk": "HK",    # 港股
    "us": "US",    # 美股
    "de": "GR",    # 德国/Xetra
    "gr": "GR",    # 德国
    "pa": "FR",    # 巴黎
    "fr": "FR",    # 法国
    "t": "JP",     # 日本
    "jp": "JP",    # 日本
    "sw": "SW",    # 瑞士
    "ch": "SW",    # 瑞士
}

# 常见无后缀美股代码映射（持仓 Excel 中未写 .US 时兜底识别）
# 避免 ONON/ATAT/LKNCY 等被默认当成 CH 去东方财富抓数据
KNOWN_US_TICKERS: Set[str] = {
    "ATAT", "ONON", "LKNCY", "SN", "MNSO", "WMT", "COST",
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META",
    "NKE", "DIS", "JPM", "V", "MA", "UNH", "HD", "PFE",
    "KO", "PEP", "MCD", "SBUX", "NFLX", "CRM", "UBER", "LYFT",
    "ABNB", "RDDT", "PLTR", "SNOW", "ZM", "SHOP", "SQ", "PYPL",
    "ROKU", "TWLO", "DDOG", "CRWD", "OKTA", "FSLY", "NET", "MDB",
}


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """在列名中按候选名查找，优先精确匹配。"""
    norm_cols = {c: c.strip() for c in df.columns}
    for cand in candidates:
        cand_low = cand.lower()
        for orig, stripped in norm_cols.items():
            if stripped.lower() == cand_low:
                return orig
    for cand in candidates:
        cand_low = cand.lower()
        for orig, stripped in norm_cols.items():
            if cand_low in stripped.lower():
                return orig
    return None


def _safe_float(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    if f is None:
        return None
    return int(f)


def _safe_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _parse_ticker(ticker_raw: str) -> tuple:
    """解析 Ticker 原始值，支持 '002444.SZ' / 'PUM.DE' / 'WMT' 等。
    返回 (ticker, exchange)。
    """
    ticker = _safe_str(ticker_raw).upper()
    if not ticker:
        return "", ""

    # 去掉常见交易所后缀
    if "." in ticker:
        parts = ticker.rsplit(".", 1)
        suffix = parts[1].lower()
        if suffix in EXCHANGE_SUFFIX_MAP:
            return parts[0], EXCHANGE_SUFFIX_MAP[suffix]
        # 未知后缀也保留原 ticker，exchange 尝试映射
        return ticker, EXCHANGE_SUFFIX_MAP.get(suffix, suffix.upper())

    # 无后缀时：已知美股代码兜底识别为 US，否则默认 CH
    if ticker in KNOWN_US_TICKERS:
        return ticker, "US"

    return ticker, ""


def _parse_category(cat_val: Any) -> int:
    """解析类别：支持数字或中文/英文名称。"""
    s = _safe_str(cat_val).lower()
    if not s:
        return 0
    # 先尝试数字
    try:
        return int(float(s))
    except (ValueError, TypeError):
        pass
    # 再尝试映射表
    for key, val in CATEGORY_MAP.items():
        if key in s:
            return val
    return 0


def _is_short_category(category: int, category_name: str) -> bool:
    """判断是否为对冲/做空方向。"""
    if category == 4:
        return True
    name_lower = category_name.lower()
    return any(k in name_lower for k in ["short", "对冲", "做空"])


def _infer_currency(exchange: str) -> str:
    """根据交易所推断货币。"""
    mapping = {
        "CH": "CNY",
        "HK": "HKD",
        "US": "USD",
        "GR": "EUR",
        "FR": "EUR",
        "JP": "JPY",
        "SW": "CHF",
    }
    return mapping.get(exchange.upper(), "")


def load_holdings(
    file_path: Optional[str] = None,
    holdings_file_default: Optional[str] = None,
    log: Optional[Any] = None,
) -> Optional[List[Dict[str, Any]]]:
    """读取新版持仓 Excel（支持多模板）。

    Args:
        file_path: 显式覆盖
        holdings_file_default: 模块默认路径（HOLDINGS_FILE 环境变量）
        log: 可选日志函数

    Returns:
        list[dict] 或 None
    """
    path = file_path or holdings_file_default or os.getenv("HOLDINGS_FILE", "holdings.xlsx")
    _log = log or (lambda m: None)

    if not path or not os.path.exists(path):
        _log(f"[ERROR] 持仓文件不存在: {path}")
        return None

    try:
        df = pd.read_excel(path, sheet_name=0, header=0)
    except Exception as e:
        _log(f"[ERROR] 读取 Excel 失败: {e}")
        return None

    # 查找列
    col_map = {}
    for key, cands in COL_CANDIDATES.items():
        col_map[key] = _find_column(df, cands)

    col_ticker = col_map["ticker"]
    col_run = col_map["run"]
    col_exchange = col_map["exchange"]
    col_category = col_map["category"]

    if not col_ticker:
        _log("[ERROR] Excel 缺少 Ticker 列")
        return None

    if not col_run:
        _log("[WARN] Excel 缺少 Run/重点分析 列，默认全部跑")

    holdings = []
    for _, row in df.iterrows():
        ticker_raw = _safe_str(row.get(col_ticker))
        if not ticker_raw:
            continue

        ticker, exchange_from_suffix = _parse_ticker(ticker_raw)
        if not ticker:
            continue

        # Exchange 列优先，否则从后缀推导，默认 CH
        exchange = _safe_str(row.get(col_exchange, "")).upper() if col_exchange else ""
        if not exchange:
            exchange = exchange_from_suffix or "CH"

        # Run 列：Y / Yes / 1 / 重点分析=Y 都算跑；空/N/0 跳过
        run_val = _safe_str(row.get(col_run, "")).lower() if col_run else "y"
        should_run = run_val in ("y", "yes", "1", "true", "t", "是")

        # 类别
        category_name = _safe_str(row.get(col_category, "")) if col_category else ""
        category = _parse_category(row.get(col_category)) if col_category else 0

        size = _safe_float(row.get(col_map["position_size"])) or 0.0
        # 如果类别是“对冲/做空”，持仓量视为负
        if _is_short_category(category, category_name):
            size = -abs(size)

        direction = "LONG" if size > 0 else ("SHORT" if size < 0 else "WATCH")

        # 合并多个 Catalyst/Downside 列为一个字符串
        catalyst = _safe_str(row.get(col_map["catalyst"])) if col_map["catalyst"] else ""
        if not catalyst:
            catalyst_cols = [c for c in df.columns if "catalyst" in c.lower()]
            catalyst_parts = [_safe_str(row.get(c)) for c in catalyst_cols if _safe_str(row.get(c))]
            catalyst = "; ".join(catalyst_parts)

        risk = _safe_str(row.get(col_map["risk"])) if col_map["risk"] else ""
        if not risk:
            downside_cols = [c for c in df.columns if "downside" in c.lower() or "risk" in c.lower()]
            risk_parts = [_safe_str(row.get(c)) for c in downside_cols if _safe_str(row.get(c))]
            risk = "; ".join(risk_parts)

        item = {
            "ticker": ticker,
            "exchange": exchange,
            "short_name": _safe_str(row.get(col_map["short_name"])),
            "category": category,
            "position_size_usd": abs(size),
            "position_direction": direction,
            "usd_mkt_cap": size,
            "eqy_fund_crncy": _safe_str(row.get(col_map["currency"])) or _infer_currency(exchange),
            "cost": _safe_float(row.get(col_map["cost"])),
            "target": _safe_float(row.get(col_map["target"])),
            "strategy": _safe_str(row.get(col_map["strategy"])),
            "catalyst": catalyst,
            "risk": risk,
            "conviction": _safe_int(row.get(col_map["conviction"])) or 0,
            "run": should_run,
            "notes": _safe_str(row.get(col_map["notes"])),
        }
        holdings.append(item)

    _log(f"[OK] load_holdings: {len(holdings)} 条持仓（来自 {path}）")
    return holdings


def load_fx(
    file_path: Optional[str] = None,
    holdings_file_default: Optional[str] = None,
    log: Optional[Any] = None,
) -> Dict[str, float]:
    """读 Sheet2 汇率表 → {currency: usd_per_unit}。

    Sheet2 表头位置约定：A1=Currency / B1=USD per unit。
    """
    path = file_path or holdings_file_default or os.getenv("HOLDINGS_FILE", "holdings.xlsx")
    _log = log or (lambda m: None)

    if not path or not os.path.exists(path):
        _log(f"[WARN] 汇率文件不存在: {path}")
        return {}

    try:
        df = pd.read_excel(path, sheet_name=1, header=0)
    except Exception as e:
        _log(f"[WARN] 读 Sheet2 汇率表失败: {e}")
        return {}

    if df.shape[1] < 2:
        _log(f"[WARN] Sheet2 列数不足 2")
        return {}

    out = {}
    for _, row in df.iterrows():
        ccy = _safe_str(row.iloc[0])
        f = _safe_float(row.iloc[1])
        if ccy and f is not None:
            out[ccy] = f
    _log(f"[OK] load_fx: {len(out)} 个货币")
    return out
