"""AKShare macro data reader for macro-bot.

Reads pre-fetched AKShare macro data from daily-news-fetcher/akshare/YYYY-MM-DD.json
and formats a concise summary for the daily briefing prompt.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AKSHARE_DIR = os.path.join(BASE_DIR, "daily-news-fetcher", "akshare")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _try_parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y年%m月份", "%Y年第%m季度", "%Y年第1-%m季度"):
        try:
            # Normalize Chinese "季度" text that contains a range; fallback to first month
            if "第1-" in s:
                s = s.replace("第1-", "第")
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _load_akshare_data(date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    date = date or _today_str()
    path = os.path.join(AKSHARE_DIR, f"{date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def format_akshare_macro_summary(date: Optional[str] = None, max_chars: int = 200) -> Optional[str]:
    """Format AKShare macro data as a one-line summary string.

    Returns None if data is missing/empty.
    """
    data = _load_akshare_data(date)
    if not data:
        return None
    macro = data.get("macro")
    if not macro:
        return None

    snippets = []
    pmi = macro.get("pmi")
    if pmi:
        snippets.append(f"PMI={pmi.get('manufacturing', 'N/A')}/{pmi.get('non_manufacturing', 'N/A')} ({pmi.get('month', 'N/A')})")
    cpi = macro.get("cpi")
    if cpi:
        snippets.append(f"CPI={cpi.get('national_yoy', 'N/A')}% YoY/{cpi.get('national_mom', 'N/A')}% MoM ({cpi.get('month', 'N/A')})")
    gdp = macro.get("gdp")
    if gdp:
        snippets.append(f"GDP={gdp.get('yoy', 'N/A')}% ({gdp.get('quarter', 'N/A')})")
    lpr = macro.get("lpr")
    if lpr:
        snippets.append(f"LPR 1Y={lpr.get('lpr_1y', 'N/A')}% 5Y={lpr.get('lpr_5y', 'N/A')}% ({lpr.get('date', 'N/A')})")

    if not snippets:
        return None
    summary = " | ".join(snippets)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


def get_akshare_sector_highlights(date: Optional[str] = None, top_n: int = 5) -> List[str]:
    """Return top gaining/losing A-share sector boards as short strings."""
    data = _load_akshare_data(date)
    if not data:
        return []
    boards = data.get("sectorBoards") or []
    if not boards:
        return []
    try:
        sorted_boards = sorted(boards, key=lambda x: float(x.get("changePct", 0) or 0), reverse=True)
    except Exception:
        return []
    lines = []
    for b in sorted_boards[:top_n]:
        name = b.get("name", "")
        pct = b.get("changePct", "")
        if name and pct not in (None, "", "N/A"):
            lines.append(f"{name} {pct}%")
    return lines


def get_akshare_latest_announcement_dates(date: Optional[str] = None, days: int = 7) -> Dict[str, List[str]]:
    """Return tickers whose AKShare articles were published within the last `days` days."""
    data = _load_akshare_data(date)
    if not data:
        return {}
    cutoff = datetime.now() - timedelta(days=days)
    result: Dict[str, List[str]] = {}
    for article in data.get("articles", []):
        published = article.get("publishedAt")
        if not published:
            continue
        dt = _try_parse_date(published)
        if not dt or dt < cutoff:
            continue
        for ticker in article.get("symbols", []):
            result.setdefault(ticker, []).append(article.get("title", ""))
    return result
