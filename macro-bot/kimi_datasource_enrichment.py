"""
kimi_datasource_enrichment.py - Kimi 专业数据源增强模块

为 macro-bot 提供基于 Kimi 会员数据源的宏观经济、学术研究和股票财务数据增强。
所有函数均带失败静默回退，避免 Kimi CLI/凭证问题导致整个 briefing 流程中断。

依赖:
    mcp>=1.0.0 (Python >= 3.10)
    kimi_datasource_client.py (同目录)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from kimi_datasource_client import run_query, run_get_desc


VALID_DATA_SOURCES = [
    "stock_finance_data",
    "yahoo_finance",
    "world_bank_open_data",
    "tianyancha",
    "arxiv",
    "scholar",
    "yuandian_law",
    "wind",
    "imf",
    "gildata",
    "sec_edgar",
    "sp_data",
]


def _is_available(data_source_name: str) -> bool:
    """Check if a data source is available by attempting to fetch its description."""
    if data_source_name not in VALID_DATA_SOURCES:
        return False
    try:
        desc = run_get_desc(data_source_name)
        return desc is not None and bool(desc.get("description_text") or desc.get("Available APIs"))
    except Exception:
        return False


def _parse_result_text(result: Optional[str]) -> Optional[str]:
    """Extract the human-readable text from a data source result."""
    if not result:
        return None
    result = result.strip()
    if result.startswith("{"):
        try:
            parsed = json.loads(result)
            if "data_preview" in parsed and parsed["data_preview"]:
                return parsed["data_preview"]
            if "is_success" in parsed:
                return parsed["is_success"]
        except json.JSONDecodeError:
            pass
    return result


# ---------- World Bank 宏观指标 ----------

DEFAULT_WORLD_BANK_MACRO_INDICATORS: List[Tuple[str, str]] = [
    ("NY.GDP.MKTP.CD", "GDP (current US$)"),
    ("NY.GDP.MKTP.KD.ZG", "GDP growth (annual %)"),
    ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)"),
    ("SL.UEM.TOTL.ZS", "Unemployment, total (% of total labor force)"),
    ("NE.TRD.GNFS.ZS", "Trade (% of GDP)"),
]


def _get_world_bank_indicator_code(keyword: str) -> Optional[str]:
    """Search World Bank indicators for the most relevant code."""
    try:
        result = run_query(
            "world_bank_open_data",
            "world_bank_search_indicators",
            {"query": keyword},
        )
        text = _parse_result_text(result)
        if not text:
            return None
        # Look for lines like "CODE: Description"
        matches = re.findall(r"([A-Z][A-Z.0-9A-Z]+):\s", text)
        if matches:
            return matches[0]
    except Exception as e:
        print(f"[WARN] world_bank_search_indicators failed: {e}")
    return None


def get_world_bank_macro(
    indicators: Optional[List[Tuple[str, str]]] = None,
    countries: Optional[List[str]] = None,
    most_recent: int = 3,
) -> Optional[str]:
    """Fetch recent World Bank macro data and return a formatted Chinese summary.

    Args:
        indicators: List of (indicator_code, display_name) tuples. If None, use defaults.
        countries: List of ISO 3-letter country codes. Defaults to ["CHN", "USA"].
        most_recent: Number of most recent years to retrieve.

    Returns:
        Formatted summary string or None on failure.
    """
    if countries is None:
        countries = ["CHN", "USA"]
    if indicators is None:
        indicators = DEFAULT_WORLD_BANK_MACRO_INDICATORS

    summaries: List[str] = []
    for code, display_name in indicators:
        try:
            result = run_query(
                "world_bank_open_data",
                "world_bank_open_data",
                {
                    "country": ",".join(countries),
                    "indicator": code,
                    "filepath": f"/tmp/world_bank_{code.replace('.', '_')}.csv",
                    "most_recent": most_recent,
                    "language": "en",
                },
            )
            text = _parse_result_text(result)
            if text:
                summaries.append(f"{display_name}: {text[:500]}")
        except Exception as e:
            print(f"[WARN] world_bank_open_data {code} failed: {e}")
            continue

    if not summaries:
        return None

    return "\n".join(
        [
            "【World Bank 全球宏观补充】",
            "数据源: world_bank_open_data (via Kimi datasource)",
            "",
            *summaries,
        ]
    )


def get_global_macro_summary(most_recent: int = 3) -> Optional[str]:
    """Convenience wrapper for China + US global macro summary."""
    return get_world_bank_macro(most_recent=most_recent)


# ---------- arXiv 研究增强 ----------


def get_arxiv_research_summary(
    query: str = "large language model finance trading",
    max_results: int = 3,
) -> Optional[str]:
    """Fetch recent arXiv papers related to a query and return a formatted summary.

    Args:
        query: Search query (max 6 words recommended by the datasource).
        max_results: Max number of papers to retrieve.

    Returns:
        Formatted summary string or None on failure.
    """
    try:
        result = run_query(
            "arxiv",
            "search_papers",
            {
                "query": query,
                "max_results": max_results,
                "file_path": "/tmp/arxiv_research.csv",
            },
        )
        text = _parse_result_text(result)
        if not text:
            return None

        return "\n".join(
            [
                "【arXiv 研究增强】",
                f"查询: {query}",
                "",
                text[:1500],
            ]
        )
    except Exception as e:
        print(f"[WARN] arxiv search failed: {e}")
        return None


# ---------- 同花顺 A 股财务数据 ----------


def get_stock_finance_data(ticker: str, exchange: str = "CH") -> Optional[str]:
    """Fetch A-share finance data from Tonghuashun via Kimi datasource.

    Args:
        ticker: Stock code without exchange suffix (e.g., '000001').
        exchange: Market code. Only CH is supported for this datasource.

    Returns:
        Formatted summary string or None on failure.
    """
    if exchange.upper() != "CH":
        return None

    # Normalize ticker into the datasource format: XXXXXX.SZ/SH/BJ
    if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", ticker):
        if ticker.startswith(("6", "9")):
            ticker = f"{ticker}.SH"
        elif ticker.startswith(("0", "3")):
            ticker = f"{ticker}.SZ"
        elif ticker.startswith(("4", "8")):
            ticker = f"{ticker}.BJ"
        else:
            return None

    try:
        result = run_query(
            "stock_finance_data",
            "stock_finance_data_get_stock_info",
            {
                "ticker": ticker,
                "file_path": "/tmp/kimi_stock_info.csv",
                "format": "json",
            },
        )
        text = _parse_result_text(result)
        if not text:
            return None

        return "\n".join(
            [
                "【同花顺财务数据】",
                f"股票代码: {ticker}",
                "",
                text[:2000],
            ]
        )
    except Exception as e:
        print(f"[WARN] stock_finance_data {ticker} failed: {e}")
        return None


# ---------- 统一增强接口 ----------


def enrich_macro_context() -> str:
    """Return a combined macro enrichment string for use in prompts.

    Safe to call: failures are silent and return empty string.
    """
    parts: List[str] = []

    # NOTE: world_bank_open_data queries frequently exceed the 30s MCP timeout,
    # so we do not include it in the default pipeline. Use get_global_macro_summary()
    # directly if you need it and can tolerate timeouts.
    arxiv = get_arxiv_research_summary(
        query="LLM quantitative trading macro",
        max_results=2,
    )
    if arxiv:
        parts.append(arxiv)

    return "\n\n".join(parts)


def enrich_stock_context(ticker: str, exchange: str = "CH") -> str:
    """Return a stock-specific enrichment string for use in prompts.

    Safe to call: failures are silent and return empty string.
    """
    parts: List[str] = []

    if exchange.upper() == "CH":
        finance = get_stock_finance_data(ticker, exchange)
        if finance:
            parts.append(finance)

    return "\n\n".join(parts)


if __name__ == "__main__":
    # Ad-hoc smoke test
    print("=== Macro enrichment ===")
    print(enrich_macro_context() or "<no enrichment>")
    print("\n=== Stock enrichment (000001) ===")
    print(enrich_stock_context("000001") or "<no enrichment>")
