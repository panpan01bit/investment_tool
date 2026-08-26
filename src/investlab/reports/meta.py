"""券商报告元信息抽取：标题 / 券商 / 日期 / 评级 / 目标价 / 涉及标的。"""

from __future__ import annotations

import re

from ..datasources.symbols import normalize, split_symbol_column
from ..utils.common import parse_date

# 常见券商与研究所关键词（按匹配优先级）
KNOWN_BROKERS = [
    "中金公司", "中信证券", "中信建投", "华泰证券", "国泰海通", "国泰君安", "申万宏源",
    "广发证券", "招商证券", "兴业证券", "东方证券", "东吴证券", "天风证券", "开源证券",
    "浙商证券", "民生证券", "西部证券", "国盛证券", "海通证券", "方正证券", "光大证券",
    "平安证券", "安信证券", "国信证券", "长江证券", "银河证券", "华西证券", "华福证券",
    "东兴证券", "中银国际", "中银证券", "瑞银", "高盛", "摩根士丹利", "摩根大通",
    "花旗", "美银", "巴克莱", "杰富瑞", "Bernstein", "麦肯锡", "赛迪顾问",
    "中国信通院", "IDC", "Gartner", "TrendForce", "Omdia", "LightCounting",
]

RATING_PATTERNS = [
    r"(?:投资评级|评级)\s*[:：]?\s*(优于大市|买入|增持|中性|持有|减持|卖出|强烈推荐|推荐|跑赢行业|跑输行业|审慎推荐)",
    r"\b(BUY|OUTPERFORM|OVERWEIGHT|NEUTRAL|HOLD|UNDERWEIGHT|SELL|MARKET PERFORM)\b",
]

TARGET_PRICE_RE = re.compile(
    r"(?:目标价|目标价格|TP|Target\s*Price)\s*[:：]?\s*(?:RMB|人民币|HKD|USD|\$|￥)?\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

TICKER_IN_TEXT = re.compile(
    r"(?:(?:股票代码|代码|Ticker)[:：]?\s*)([0-9]{6}[.\-]?(?:SS|SZ|SH)?|[0-9]{4,5}\.?HK?|[A-Z]{1,5}(?:\s*[.]\s*[NU]?))",
)

CN_STOCK_MENTION = re.compile(
    r"([0-9]{6})[.\-](SS|SZ|SH)\b"
)


def detect_broker(text_head: str, filename: str = "") -> str:
    blob = f"{filename} {text_head[:4000]}"
    for b in KNOWN_BROKERS:
        if b in blob:
            return b
    m = re.search(r"([^\s，,。·|-]{2,10}(?:证券|研究|研究院|资本|Investment))", text_head[:2000])
    if m:
        return m.group(1)[:12]
    return ""


def detect_title(first_page_text: str) -> str:
    """取前几行中最像标题的一行：长度适中、含研究关键词或证券代码、非叙述句。"""
    lines = [_collapse(ln) for ln in first_page_text.splitlines()]
    lines = [ln.strip() for ln in lines if ln.strip()]
    candidates = []
    keywords = ("研究", "分析", "点评", "跟踪", "深度", "报告", "展望", "策略",
                "行业", "首次覆盖", "更新", "调研", "纪要")
    stop = ("证券", "请务必阅读", "免责声明", "分析师", "评级", "目标价",
            "www.", "http", "电话", "邮箱", "资料来源")
    for i, ln in enumerate(lines[:40]):
        if any(s in ln for s in stop):
            continue
        if not (6 <= len(ln) <= 60):
            continue
        if ln.endswith(("。", "，", "；", "、")):  # 叙述句不是标题
            continue
        score = min(len(ln), 50) / 5                # ≤10
        score += min(sum(1 for k in keywords if k in ln), 3) * 6   # ≤18
        if re.search(r"[（(]\d{6}(?:\.[A-Z]{2})?[）)]", ln):
            score += 6                              # 含代码括号，强信号
        score -= i * 0.8
        candidates.append((score, ln))
    if not candidates:
        return lines[0][:60] if lines else ""
    return _collapse(max(candidates)[1])


def _collapse(s: str) -> str:
    """任何空白序列（含换行/软换行）折叠为单空格——标题必须是单行。"""
    import re as _re

    return _re.sub(r"\s+", " ", str(s)).strip()


def extract_meta(text_by_page: list[str], filename: str = "") -> dict:
    full_head = "\n".join(text_by_page[:3])
    full_all = "\n".join(text_by_page)

    meta = {
        "title": detect_title(full_head),
        "broker": detect_broker(full_head, filename),
        "date": None,
        "rating": None,
        "target_price": None,
        "symbols": [],
        "analysts": [],
    }

    # 日期：优先报告首页形如 2026年7月28日 / 2026-07-28
    for pat in (
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}\d{2}\d{2})",
    ):
        m = re.search(pat, full_head)
        if m:
            d = parse_date(m.group(1))
            if d:
                meta["date"] = d.isoformat()
                break

    for pat in RATING_PATTERNS:
        m = re.search(pat, full_all[:8000], re.IGNORECASE)
        if m:
            meta["rating"] = m.group(1)
            break

    m = TARGET_PRICE_RE.search(full_all[:8000])
    if m:
        meta["target_price"] = float(m.group(1))

    # 标的：显式“代码：” > 文内 6位.SS/SZ 形态
    found: dict[str, str] = {}
    for m in TICKER_IN_TEXT.finditer(full_head):
        n = normalize(m.group(1))
        if _plausible(n) and n not in _FINANCE_ACRONYMS:
            found.setdefault(n, "")
    if not found:
        for m in CN_STOCK_MENTION.finditer(full_all[:20000]):
            n = normalize(m.group(0))
            found.setdefault(n, "")
    # 尽力找标的简称：括号形态最可靠；裸 token 必须含数字或带后缀，防把 AI/GDP 当 ticker
    ctx_lines = full_head.splitlines()
    for line in ctx_lines[:40]:
        line_clean = re.sub(r"\s+", " ", line.strip())
        sym_guess, name_guess = split_symbol_column(line_clean)
        if _plausible(sym_guess) and name_guess and _bare_token_ok(sym_guess):
            found.setdefault(sym_guess, name_guess)
    paren_re = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9·]{2,20})[（(]([^）)]{1,12})[)）]")
    for m in paren_re.finditer(full_head):
        cand = re.sub(r"^(?:股票)?代码[:：]", "", m.group(2).strip().upper())
        n = normalize(cand)
        if not _plausible(n):
            continue
        ctx_window = full_head[max(0, m.start() - 40): m.start()]
        # 纯字母候选：仅当近处有显式“代码/Ticker”标注才采信（否则 Capex/GDP 一堆假阳性）
        if not (_bare_token_ok(n)
                or re.search(r"(股票代码|代码|Ticker)", ctx_window, re.IGNORECASE)):
            continue
        if n in _FINANCE_ACRONYMS:
            continue
        found.setdefault(n, m.group(1).strip()[:20])
    meta["symbols"] = [{"symbol": s, "name": n} for s, n in found.items()][:8]

    for m in re.finditer(r"分析师[:：]?\s*([\u4e00-\u9fa5]{2,4})(?:\s|，|$)", full_head):
        a = m.group(1).strip()
        if a and a not in meta["analysts"]:
            meta["analysts"].append(a)
        if len(meta["analysts"]) >= 4:
            break
    return meta


def _plausible(sym: str) -> bool:
    if not sym:
        return False
    if re.match(r"^\d{6}\.(SS|SZ)$", sym):
        return True
    if sym.endswith(".HK"):
        return True
    return bool(re.match(r"^[A-Z]{1,5}$", sym))


def _bare_token_ok(sym: str) -> bool:
    """行内裸 token 额外约束：必须含数字或带交易所后缀（纯字母一律要求括号形态）。"""
    if not sym:
        return False
    if re.search(r"\d", sym):
        return True
    return bool(re.search(r"\.(HK|SS|SZ|N|U)$", sym, re.IGNORECASE))


# 常见财经缩写，绝不允许被当作美股代码
_FINANCE_ACRONYMS = {
    "AI", "GDP", "GNP", "TAM", "SAM", "SOM", "EPS", "ROE", "ROA", "ROI",
    "KPI", "CPI", "PPI", "PMI", "LPR", "ETF", "PE", "PB", "PS", "CAPEX",
    "OPEX", "EBIT", "EBITDA", "FOMC", "MSCI", "IDC",
    "GPU", "CPU", "HBM", "PCB", "CPO", "AEC", "DAC", "BBU", "HVDC",
    "CCL", "ASIC", "SAAS", "CAGR", "NAV", "IPO", "YOY",
    "QOQ", "USD", "HKD", "CNY", "RMB", "US", "UK", "EU",
}
