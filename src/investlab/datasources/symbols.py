"""标的代码规范化：A股(6位)/港股/美股 ↔ 各数据源代码格式。

内部统一使用「exchange-qualified」形式：
  A股: 600519.SS / 000333.SZ / 300750.SZ
  港股: 00700.HK
  美股: NVDA / MU（纯字母）
"""

from __future__ import annotations

import re

RE_A_CODE = re.compile(r"^\d{6}$")
RE_HK_TAIL = re.compile(r"^0*(\d{1,5})\.?HK$", re.IGNORECASE)
RE_HK_DIGITS = re.compile(r"^0*(\d{1,5})$")
RE_US_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$", re.IGNORECASE)

SH_PREFIXES = ("60", "68")     # 沪主板 / 科创板
SZ_PREFIXES = ("00", "30")     # 深主板 / 创业板
BJ_PREFIXES = ("4", "8")       # 北交所（首位）


def market_of(symbol: str) -> str:
    """返回 CH / HK / US。"""
    s = normalize(symbol)
    if not s:
        return "US"
    if s.endswith(".SS") or s.endswith(".SZ"):
        return "CH"
    if s.endswith(".HK"):
        return "HK"
    return "US"


def normalize(raw: str) -> str:
    """任意常见写法 → 内部规范形式。

    >>> normalize("600519")
    '600519.SS'
    >>> normalize("000333.SZ")
    '000333.SZ'
    >>> normalize("700.hk")
    '00700.HK'
    >>> normalize("NVDA")
    'NVDA'
    """
    if raw is None:
        return ""
    s = str(raw).strip().upper().replace(" ", "")
    if not s:
        return ""

    if s.endswith(".HK"):
        raw = s.replace(".HK", "")
        m = RE_HK_DIGITS.match(raw)
        if m:
            return f"{int(m.group(1)):05d}.HK"
        return s
    tail = RE_HK_TAIL.match(s)
    if tail:
        return f"{int(tail.group(1)):05d}.HK"

    plain_digits = re.sub(r"\.(SS|SZ|SH|SSE|SZSE)$", "", s)
    if RE_A_CODE.match(plain_digits):
        # 沪主板/科创板（60/68）与北交所（首位 4/8）挂 .SS，其余挂 .SZ
        if plain_digits[:2] in SH_PREFIXES or plain_digits[0] in BJ_PREFIXES:
            return f"{plain_digits}.SS"
        return f"{plain_digits}.SZ"

    return s


def akshare_symbol(symbol: str) -> str:
    """内部格式 → akshare 常用「带交易所前缀」格式（如 sh600519）。"""
    s = normalize(symbol)
    if s.endswith(".SS"):
        return f"sh{s[:6]}"
    if s.endswith(".SZ"):
        return f"sz{s[:6]}"
    return s


def eastmoney_secid(symbol: str) -> str:
    """内部格式 → 东财 push2 secid（如 1.600519 / 0.000333 / 116.00700）。"""
    s = normalize(symbol)
    if s.endswith(".SS"):
        return f"1.{s[:6]}"
    if s.endswith(".SZ"):
        return f"0.{s[:6]}"
    if s.endswith(".HK"):
        return f"116.{s[:5]}"
    return f"105.{s}"  # 美股走东财美股市场（105=NASDAQ，106=NYSE，粗略默认 NASDAQ）


def yfinance_symbol(symbol: str) -> str:
    """内部格式 → yfinance 格式（A股 600519.SS/000333.SZ；港 0700.HK；美原样）。"""
    s = normalize(symbol)
    if s.endswith(".HK"):
        return f"{s[:5].lstrip('0')}.HK"
    return s


def tencent_symbol(symbol: str) -> str:
    """内部格式 → 腾讯行情代码（sh600519 / sz000333 / hk00700）。"""
    s = normalize(symbol)
    if s.endswith(".SS"):
        return f"sh{s[:6]}"
    if s.endswith(".SZ"):
        return f"sz{s[:6]}"
    if s.endswith(".HK"):
        return f"hk{s[:5]}"
    return f"us{s}"


def display_name_fallback(symbol: str) -> str:
    """拿不到中文名时的展示名。"""
    return symbol


def split_symbol_column(value: str) -> tuple[str, str]:
    """'600519.SS 贵州茅台' 或 '贵州茅台(600519.SS)' → (symbol, name)。尽力解析。"""
    v = value.strip()
    paren = re.match(r"^(.+?)\(([^)]+)\)", v)
    if paren:
        name, sym = paren.group(1).strip(), paren.group(2).strip()
        return normalize(sym), name
    tokens = v.split()
    for t in tokens:
        n = normalize(t)
        if n and (
            re.match(r"^\d{6}", n) or n.endswith(".HK") or RE_US_TICKER.match(n)
        ):
            name = " ".join(x for x in tokens if x != t).strip()
            return n, name
    return normalize(tokens[0]) if tokens else "", ""
