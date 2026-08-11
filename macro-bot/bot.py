"""
宏观预警机器人 - 重写版 (T1 + T4 集成)
=====================================
T1 修复点（相对原 bot.py）：
  1. 密钥全部走环境变量 / .env；不再硬编码 Webhook 与 Kimi Key
  2. load_holdings() 适配 Bloomberg 三行表头（header=[0,1,2]），列名扁平化
  3. load_fx() 读 Sheet2 汇率表
  4. get_stock_data() 保留东方财富 CH 实现；非 CH 返回 None 并打 TODO，
     FinanceMCP 多市场接入留给 T4
  5. generate_signal() 调 model_router.call_kimi_with_router
     （bot.py 不直接调 Kimi，模型选择/降级/埋点由 T3 负责）
  6. send_feishu() 改用 post（富文本）消息
  7. 兼容 Cost/Volume 可选列（缺失 log warning，不崩）

T4 集成点（本任务）：
  - import mcp_client（FinanceMCP HTTP 客户端）
  - get_stock_data() 非 CH 分支调 mcp_client.get_technical_signals()
  - generate_briefing() 新增宏观摘要（mcp_client.get_macro_summary，
    共享 1 次给所有持仓）
  - generate_briefing() 新增前 5 大持仓新闻（mcp_client.get_recent_news）
  - 成本控制：stock_data 只对 >= $10M 持仓调；news 只对前 5 大持仓调
  - 节流：5s 调用间隔由 mcp_client._throttle 保证

部署：见 README_BOT.md
"""

import os
import time
import hmac
import hashlib
import base64
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import re
import requests
from dotenv import load_dotenv

# 顶部加载 .env
load_dotenv('/www/wwwroot/macro-bot/.env')


# ========== 配置（全部从 env 读取） ==========
FEISHU_WEBHOOK: str = os.getenv("FEISHU_WEBHOOK", "")
FEISHU_SIGN_SECRET: str = os.getenv("FEISHU_SIGN_SECRET", "")
# 飞书加签时间戳单位：ms（当前实现）/ s（部分文档）。可通过 FEISHU_SIGN_TIMESTAMP_UNIT=ms|s 切换。
FEISHU_SIGN_TS_UNIT = os.getenv("FEISHU_SIGN_TIMESTAMP_UNIT", "ms")
BRIEFING_OUTPUT_DIR: str = os.getenv("BRIEFING_OUTPUT_DIR", "/www/wwwroot/macro-bot/briefings")


def _feishu_sign(timestamp: str, secret: str) -> str:
    """计算飞书签名 (HMAC-SHA256, base64)。
    飞书自定义机器人签名算法 (官方文档 https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot):
      string_to_sign = timestamp + "\\n" + secret
      hmac_code = hmac.new(string_to_sign.encode(), digestmod=sha256).digest()
      sign = base64(hmac_code)
    注意: timestamp 必须是**毫秒**(time.time() * 1000), 不是秒
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")
KIMI_API_KEY: str = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_MODEL: str = os.getenv("KIMI_BASE_MODEL", "kimi-k2.5")
KIMI_STRONG_MODEL: str = os.getenv("KIMI_STRONG_MODEL", "kimi-k2-thinking")
MCP_HTTP_URL: str = os.getenv("MCP_HTTP_URL", "http://localhost:3000/mcp")
HOLDINGS_FILE: str = os.getenv("HOLDINGS_FILE", "holdings.xlsx")
LOG_FILE: str = os.getenv("LOG_FILE", "run.log")

# Kimi 调用参数（与 PRD V2 一致）
KIMI_TIMEOUT: int = int(os.getenv("KIMI_TIMEOUT", "120"))
KIMI_MAX_RETRIES: int = int(os.getenv("KIMI_MAX_RETRIES", "2"))
KIMI_MAX_TOKENS: int = int(os.getenv("KIMI_MAX_TOKENS", "2500"))
KIMI_BASE_URL: str = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")

# USD mkt cap 阈值（LONG/SHORT 判定）
USD_MKTCAP_LONG_THRESHOLD: float = 0.0

# model_router 注入（T3 任务交付；缺失时给一个明确报错的 stub）
try:
    from model_router import call_kimi_with_router  # type: ignore
except ImportError:  # pragma: no cover
    def call_kimi_with_router(
        holding: Dict[str, Any],
        signal_strength: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Stub：T3 任务(model-router)未部署时使用。"""
        raise RuntimeError(
            "model_router.call_kimi_with_router 未找到。请先部署 T3 (model-router) "
            "或在同目录提供 model_router.py。"
        )


# ========== 日志 ==========
def log(msg: str) -> None:
    """统一日志入口。控制台 + 文件双写。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[{timestamp}] log-file write failed: {e}")


# ========== Excel 解析（拆到 holdings.py） ==========
from holdings import load_holdings, load_fx  # noqa: E402


# ========== FinanceMCP 客户端（T4 集成） ==========
try:
    from mcp_client import (  # type: ignore
        get_macro_summary,
        get_technical_signals,
        get_recent_news,
        to_ts_code,
        STOCK_DATA_MIN_SIZE_USD,
        NEWS_MAX_HOLDINGS,
    )
except ImportError:  # pragma: no cover
    # 兼容 mcp_client 缺失场景（仅在测试或极端部署下触发）
    def get_macro_summary(*args, **kwargs):
        log("[WARN] mcp_client 缺失，宏观摘要不可用")
        return None

    def get_technical_signals(*args, **kwargs):
        log("[WARN] mcp_client 缺失，技术指标不可用")
        return None

    def get_recent_news(*args, **kwargs):
        log("[WARN] mcp_client 缺失，新闻不可用")
        return None

    def to_ts_code(ticker, exchange):
        return (ticker, "other")

    STOCK_DATA_MIN_SIZE_USD = 10_000_000.0
    NEWS_MAX_HOLDINGS = 5

# ========== Kimi 专业数据源增强（T8 集成） ==========
try:
    from kimi_datasource_enrichment import (  # type: ignore
        enrich_macro_context,
        enrich_stock_context,
    )
except ImportError:  # pragma: no cover
    def enrich_macro_context() -> str:
        return ""

    def enrich_stock_context(ticker: str, exchange: str = "CH") -> str:
        return ""


# ========== T5: 新闻聚合模块 ==========
try:
    from news_fetcher import (
        fetch_news_for_holding,
        format_news_for_prompt,
        DEFAULT_NEWS_HOURS_WINDOW,
    )
except ImportError:
    # 兼容旧版：news_fetcher 缺失时回退到 mcp_client
    def fetch_news_for_holding(holding, **kwargs):
        sn = holding.get("short_name") or holding.get("ticker", "")
        return get_recent_news(sn, limit=3) or []

    def format_news_for_prompt(news_items):
        if not news_items:
            return "暂无新闻。"
        lines = ["【相关新闻】"]
        for i, t in enumerate(news_items, 1):
            lines.append("%d. %s" % (i, t))
        return "\n".join(lines)

    DEFAULT_NEWS_HOURS_WINDOW = 24


# ========== AKShare 宏观数据读取 ==========
def _load_akshare_macro(date=None):
    """Load AKShare macro/sector data from the daily-news-fetcher raw output."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "daily-news-fetcher",
        "akshare",
        f"{date}.json",
    )
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f).get("macro")
    except Exception as e:
        log("[WARN] 读取 AKShare 宏观数据失败 %s: %s" % (p, e))
        return None


def _format_akshare_macro(macro):
    """Return a concise Chinese macro summary from AKShare indicators."""
    if not macro:
        return ""
    parts = []
    m = macro or {}
    if m.get("pmi"):
        p = m["pmi"]
        parts.append(
            "PMI(%s) 制造业%s, 非制造业%s"
            % (p.get("month", ""), p.get("manufacturing", ""), p.get("non_manufacturing", ""))
        )
    if m.get("cpi"):
        c = m["cpi"]
        parts.append(
            "CPI(%s) 同比%s, 环比%s"
            % (c.get("month", ""), c.get("national_yoy", ""), c.get("national_mom", ""))
        )
    if m.get("gdp"):
        g = m["gdp"]
        parts.append(
            "GDP(%s) 同比%s, 绝对值%s"
            % (g.get("quarter", ""), g.get("yoy", ""), g.get("value", ""))
        )
    if m.get("lpr"):
        l = m["lpr"]
        parts.append(
            "LPR(%s) 1Y%s / 5Y%s"
            % (l.get("date", ""), l.get("lpr_1y", ""), l.get("lpr_5y", ""))
        )
    if not parts:
        return ""
    return "; ".join(parts)


def _get_macro_summary_with_fallback(months=3, max_chars=200):
    """FinanceMCP macro summary with AKShare fallback.

    If FinanceMCP returns a string where most indicators are N/A, prefer the
    AKShare summary (which has real values for PMI/CPI/GDP/LPR).
    """
    fm_summary = get_macro_summary(months=months, max_chars=max_chars)
    usable = False
    if fm_summary and fm_summary.strip():
        s = fm_summary.strip()
        if s not in ("N/A", "NA", "n/a"):
            # Count number of indicators that have real data
            indicators = re.split(r"[\|,;]", s)
            values = [v.split("=")[-1].strip() for v in indicators if "=" in v]
            non_empty = [v for v in values if v and v.upper() not in ("N/A", "NA", "NONE", "NULL")]
            if values and len(non_empty) / len(values) >= 0.5:
                usable = True
    if usable:
        log("[INFO] 使用 FinanceMCP 宏观摘要")
        return fm_summary
    ak_macro = _load_akshare_macro()
    ak_summary = _format_akshare_macro(ak_macro)
    if ak_summary:
        log("[INFO] FinanceMCP 宏观摘要可用指标不足，使用 AKShare 宏观摘要")
        return ak_summary
    if fm_summary:
        log("[INFO] 使用 FinanceMCP 宏观摘要（AKShare 无数据）")
        return fm_summary
    log("[WARN] 宏观摘要不可用")
    return ""


# ========== 实时行情 ==========
def get_stock_data(
    ticker: str,
    exchange: str = "CH",
    fx_table: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """抓实时行情。

    T1 阶段：
        * CH 走东方财富 push2 接口
        * 其他市场返回 None + log [TODO/T4]
    T4 阶段（本任务）：
        * CH 仍然走东方财富（最稳定，A 股实时最优）
        * 非 CH 走 FinanceMCP stock_data（多市场统一）
        * 返回值新增 'mcp_technical' 字段（MCP 返回的完整文本）

    Args:
        ticker: 股票代码（不含 exchange 后缀；如 '688169' 或 'PUM'）
        exchange: 市场代码（CH/HK/US/JP/GR/...）
        fx_table: 汇率表（来自 load_fx()），目前仅日志使用

    Returns:
        dict: {name, price, prev_close, change_pct, high, low, volume, mcp_technical}
              或 None
    """
    ticker = str(ticker).strip()
    ex = (exchange or "CH").upper()

    # CH 仍走东方财富
    if ex == "CH":
        try:
            if ticker.startswith("6") or ticker.startswith("9"):
                secid = f"1.{ticker}"
            else:
                secid = f"0.{ticker}"
            url = (
                "http://push2.eastmoney.com/api/qt/stock/get"
                f"?secid={secid}&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f170"
            )
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            if not data.get("data"):
                log(f"[WARN] 东方财富无数据 {ticker}")
                return None
            s = data["data"]
            return {
                "name": s.get("f58", "未知"),
                "price": round(int(s.get("f43", 0)) / 100, 2) if s.get("f43") else 0,
                "prev_close": round(int(s.get("f60", 0)) / 100, 2) if s.get("f60") else 0,
                "change_pct": round(int(s.get("f170", 0)) / 100, 2) if s.get("f170") else 0,
                "high": round(int(s.get("f44", 0)) / 100, 2) if s.get("f44") else 0,
                "low": round(int(s.get("f45", 0)) / 100, 2) if s.get("f45") else 0,
                "volume": int(s.get("f47", 0)),
            }
        except Exception as e:
            log(f"[ERROR] 抓 {ticker} 数据失败: {e}")
            return None

    # 非 CH → FinanceMCP（T4 集成）
    technical = get_technical_signals(ticker, ex)
    if not technical:
        log(f"[WARN] FinanceMCP {ticker}.{ex} 无数据")
        return None
    return {
        "name": ticker,  # MCP 返回的文本里已含名称，此处不重复
        "price": None,
        "prev_close": None,
        "change_pct": None,
        "high": None,
        "low": None,
        "volume": None,
        "mcp_technical": technical,  # 完整行情 + 技术指标文本（给 prompt 用）
    }


# ========== Prompt 模板 ==========
def load_prompt_template() -> str:
    """读取 PROMPT.md 模板（系统提示词），文件不存在则返回空串。"""
    for p in [os.getenv("PROMPT_FILE", "PROMPT.md"), "PROMPT.md", "prompt.md"]:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                log(f"[WARN] 读 PROMPT 模板失败 {p}: {e}")
    return ""


# ========== Kimi 调用（通过 model_router 间接调用） ==========
def _build_user_prompt(
    holding,
    market,
    macro_summary=None,
    news_text=None,
    kimi_enrichment=None,
):
    """Construct Kimi user prompt (T5 enhanced with structured news text, T8 enhanced with Kimi datasource)."""
    md = market or {}
    lines = [
        "【持仓数据】",
        "- 代码: %s.%s" % (holding.get("ticker"), holding.get("exchange")),
        "- 名称: %s" % holding.get("short_name"),
        "- 分类: Category %s" % holding.get("category", 1),
        "- 方向: %s" % holding.get("position_direction"),
        "- 仓位大小: $%s USD" % "{:,.0f}".format(holding.get("position_size_usd", 0) or 0),
        "- 交易货币: %s" % holding.get("eqy_fund_crncy"),
        "- 最新价: %s %s" % (holding.get("px_last"), holding.get("eqy_fund_crncy")),
    ]

    if holding.get("cost") is not None:
        lines.append("- 成本价: %s" % holding.get("cost"))
    if holding.get("target") is not None:
        lines.append("- 目标价: %s" % holding.get("target"))
    if holding.get("volume") is not None:
        lines.append("- 持仓量: %s" % holding.get("volume"))

    if holding.get("strategy"):
        lines.append("")
        lines.append("【关键逻辑】")
        lines.append(holding.get("strategy"))
    if holding.get("catalyst"):
        lines.append("")
        lines.append("【催化剂】")
        lines.append(holding.get("catalyst"))
    if holding.get("risk"):
        lines.append("")
        lines.append("【风险】")
        lines.append(holding.get("risk"))
    if holding.get("conviction"):
        lines.append("")
        lines.append("【确信度】: %s/5" % holding.get("conviction"))

    # Market data
    if any(md.get(k) is not None for k in ("price", "change_pct", "high", "low", "volume")):
        lines.append("")
        lines.append("【市场数据（今日）】")
        lines.append("- 现价: %s" % md.get("price"))
        lines.append("- 涨跌: %s%%" % md.get("change_pct"))
        lines.append("- 最高: %s" % md.get("high"))
        lines.append("- 最低: %s" % md.get("low"))
        lines.append("- 成交量: %s" % md.get("volume"))

    # Technical indicators
    if md.get("mcp_technical"):
        lines.append("")
        lines.append("【技术指标（FinanceMCP / 近 30 日）】")
        lines.append(md["mcp_technical"])

    # Macro summary
    if macro_summary:
        lines.append("")
        lines.append("【宏观摘要（近 3 月）】")
        lines.append(macro_summary)

    # T8: Kimi datasource enrichment (arxiv research / tonghuashun financial data)
    if kimi_enrichment:
        lines.append("")
        lines.append("【Kimi 专业数据源补充】")
        lines.append(kimi_enrichment)

    # T5: Structured news text
    if news_text:
        lines.append("")
        lines.append("【新闻/事件】")
        lines.append("请严格区分以下两类：\n· 【今日焦点（过去24小时）】必须作为主体分析；\n· 【近30日背景】仅作为补充背景，不准展开分析。")
        lines.append("")
        lines.append(news_text)

    return "\n".join(lines)


def generate_signal(
    holding,
    market=None,
    signal_strength="base",
    macro_summary=None,
    news_text=None,
    kimi_enrichment=None,
):
    """Generate AI signal for single holding via model_router."""
    template = load_prompt_template()
    system_prompt = (
        "你是买方基金宏观分析师，观点必须明确，禁止模棱两可。\n"
        "\n"
        "【输出格式】\n"
        "1) 第一行: 红绿灯信号 (🟢 加仓 / 🟡 持有观望 / 🔴 减仓 / ⚪ 跳过)\n"
        "2) 接下来分三段（总字数严格控制在200字以内）：\n"
        "   - 新闻/事件：先宫告最重大的新闻事件是什么（什么事情、什么时候、什么人/机构）\n"
        "   - 对公司的影响：这个新闻对这只股票的具体影响是什么（利好/利空/中性，影响渠道、影响程度）\n"
        "   - 市场 implication：昨晚市场变动的可能原因，以及对今天市场的启示\n"
        "\n"
        "【分析要求】\n"
        "- 重大新闻优先谈新闻本身，然后谈对覆盖公司的影响\n"
        "- 多谈 company-specific 的东西，少谈空泛的宏观概念\n"
        "- 涵盖昨晚市场变动原因和今天市场 implication\n"
        "- 不要写仓位金额、方向、分类等数字信息\n"
        "- 不要重复'红绿灯信号'这个标签，直接写信号和分析\n"
        "- 新闻分析必须严格区分以下两类：\n"
        "   · 【今日焦点（过去24小时）】：这是主体分析内容，必须说明具体事件、发生时间、影响；\n"
        "   · 【近30日背景】：仅作为辅助背景，不准当作主体分析，最多一句话带过。\n"
        "- 如果【今日焦点】为空，则新闻/事件段落直接写：过去24小时无重大新闻事件；对公司的影响/市场implication基于现有持仓逻辑和近期背景简要判断，不要编造新闻。\n"
    )
    if template:
        system_prompt = "%s\n\n%s" % (template, system_prompt)

    user_prompt = _build_user_prompt(holding, market, macro_summary, news_text, kimi_enrichment) + (
        "\n\n请基于以上数据和规则，生成分析信号。严格遵循输出格式。"
    )

    try:
        text = call_kimi_with_router(
            holding=holding,
            signal_strength=signal_strength,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        if not text:
            return "🟡 观察 - Kimi 返回空内容"
        return text
    except Exception as e:
        log("[ERROR] call_kimi_with_router 失败 %s: %s" % (holding.get("ticker"), e))
        return "🟡 观察 - 调用失败: %s" % e


# ========== 飞书推送 ==========
def send_feishu(
    title: str,
    sections: List[List[Dict[str, str]]],
    *,
    webhook: Optional[str] = None,
) -> bool:
    """发送 post（富文本）消息到飞书群。

    Args:
        title: 消息标题（zh_cn.title）
        sections: 富文本段落；每段是 list[ {tag, text} ]
        webhook: 覆盖默认 FEISHU_WEBHOOK（仅测试用）
    """
    url = webhook or FEISHU_WEBHOOK
    if not url:
        log("[ERROR] FEISHU_WEBHOOK 未配置")
        return False
    payload = {
        "msg_type": "post",
        "content": {"post": {"zh_cn": {"title": title, "content": sections}}},
    }
    # T6-deploy: 飞书签名校验 (在 webhook 启用了"加签"时)
    if FEISHU_SIGN_SECRET:
        timestamp = (
            str(int(time.time())) if FEISHU_SIGN_TS_UNIT == "s"
            else str(int(time.time() * 1000))
        )
        sign = _feishu_sign(timestamp, FEISHU_SIGN_SECRET)
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    try:
        resp = requests.post(url, json=payload, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            log("[OK] 飞书发送成功")
            return True
        log(f"[ERROR] 飞书发送失败: {result}")
        return False
    except Exception as e:
        log(f"[ERROR] 飞书异常: {e}")
        return False


# ========== 主流程 ==========
CAT_NAMES = {
    0: "Watchlist / 重点关注",
    1: "核心持仓 (Category 1)",
    2: "成长型 (Category 2)",
    3: "价值型 (Category 3)",
    4: "对冲/做空 (Category 4)",
}


def _section(text: str) -> List[Dict[str, str]]:
    return [{"tag": "text", "text": text}]


def generate_briefing(holdings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """生成简报内容（返回结构化 sections，供 send_feishu 使用）。

    新版流程（T7 模板化持仓）：
        1. 读 Excel → 持仓列表
        2. 只保留 Run=Y 的持仓
        3. HOLDINGS_LIMIT 作为可选兜底（截前 N 大）
        4. 调 mcp_client.get_macro_summary() 拿宏观摘要（共享 1 次）
        5. 对每只持仓：get_stock_data() → 行情/技术 → Kimi signal
        6. WATCHLIST 中的 ticker 也做 AI 分析（合并去重）
        7. 拼装飞书 post 消息
    """
    if holdings is None:
        holdings = load_holdings(file_path=None, holdings_file_default=HOLDINGS_FILE, log=log) or []
    log(f"[INFO] 开始生成简报，持仓 {len(holdings)} 条")

    # 1. 只跑 Run=Y 的持仓
    active_holdings = [h for h in holdings if h.get("run", True)]
    log(f"[INFO] Run=Y 的持仓: {len(active_holdings)} 条")

    # 2. HOLDINGS_LIMIT 作为可选兜底（截前 N 大）
    _holdings_limit = int(os.getenv("HOLDINGS_LIMIT", "0"))
    if _holdings_limit > 0 and len(active_holdings) > _holdings_limit:
        active_holdings = sorted(
            active_holdings,
            key=lambda h: h.get("position_size_usd", 0) or 0,
            reverse=True,
        )[:_holdings_limit]
        log(f"[INFO] HOLDINGS_LIMIT={_holdings_limit}, 截取前 {len(active_holdings)} 大")

    # 3. WATCHLIST：也做完整 AI 分析（合并到 active_holdings，去重）
    watchlist = [x.strip() for x in os.getenv("WATCHLIST", "").split(",") if x.strip()]
    if watchlist:
        log(f"[INFO] WATCHLIST 共 {len(watchlist)} 项: {watchlist}")
    watchlist_holdings = []
    for item in watchlist:
        # 格式支持: Ticker.Exchange 或 Ticker（默认 CH）
        if "." in item:
            ticker, ex = item.split(".", 1)
        else:
            ticker, ex = item, "CH"
        # 已在 active_holdings 中的跳过
        if any(h.get("ticker") == ticker and h.get("exchange") == ex for h in active_holdings):
            log(f"[INFO] {ticker}.{ex} 已在 Run 列表中，WATCHLIST 去重跳过")
            continue
        watchlist_holdings.append({
            "ticker": ticker,
            "exchange": ex.upper(),
            "short_name": ticker,
            "category": 0,
            "position_size_usd": 0,
            "position_direction": "WATCH",
            "usd_mkt_cap": 0,
            "eqy_fund_crncy": "",
            "cost": None,
            "target": None,
            "strategy": "",
            "catalyst": "",
            "risk": "",
            "conviction": None,
            "run": True,
            "notes": "WATCHLIST",
        })
    active_holdings = active_holdings + watchlist_holdings

    # ===== 1 次宏观摘要（共享给所有持仓） =====
    log("[INFO] 调 FinanceMCP 宏观摘要 (cpi/ppi/cn_pmi)...")
    macro_summary = _get_macro_summary_with_fallback(months=3, max_chars=200)

    # ===== T8: Kimi 专业数据源宏观增强（共享 1 次） =====
    log("[INFO] 调 Kimi 专业数据源宏观增强 (arxiv research)...")
    kimi_macro_enrichment = enrich_macro_context()
    if kimi_macro_enrichment:
        macro_summary = "\n\n".join(
            [s for s in [macro_summary, kimi_macro_enrichment] if s]
        )

    # ===== 新闻聚合（严格24小时） =====
    log("[INFO] 开始新闻聚合（24h窗口）...")
    news_map = {}
    for h in active_holdings:
        sn = h.get("short_name") or h.get("ticker", "")
        log(f"[INFO] 抓 {sn} 新闻...")
        news_items = fetch_news_for_holding(
            h,
            hours_window=int(os.getenv("NEWS_HOURS_WINDOW", "24")),
            max_results=int(os.getenv("NEWS_MAX_RESULTS", "5")),
        )
        if news_items:
            news_map[sn] = news_items
            log(f"[OK] {sn}: {len(news_items)} 条新闻")
        else:
            log(f"[WARN] {sn}: 无新闻")

    # ===== 按 category 分组 =====
    cats: Dict[int, List[Dict[str, Any]]] = {}
    for h in active_holdings:
        cats.setdefault(h.get("category", 0), []).append(h)

    title = f"📊 宏观早报 [{datetime.now().strftime('%Y-%m-%d')}]"
    sections: List[List[Dict[str, str]]] = []
    sections.append(_section(f"生成时间: {datetime.now().strftime('%H:%M')}"))
    sections.append(_section(f"分析总数: {len(active_holdings)}只（持仓 {len(active_holdings) - len(watchlist_holdings)} + Watchlist {len(watchlist_holdings)}）"))
    sections.append(_section(""))

    if macro_summary:
        sections.append(_section("【宏观摘要（近 3 月）】"))
        sections.append(_section(macro_summary))
        sections.append(_section(""))
    sections.append(_section("【持仓信号】"))
    sections.append(_section(""))

    for cat_id in sorted(cats.keys()):
        cat_name = CAT_NAMES.get(cat_id, f"Category {cat_id}")
        cat_holdings = cats[cat_id]
        sections.append(_section(f"▶ {cat_name} — {len(cat_holdings)}只"))
        sections.append(_section(""))

        for h in cat_holdings:
            ticker = h.get("ticker", "")
            ex = h.get("exchange", "")
            name = h.get("short_name", "")
            size = h.get("position_size_usd", 0) or 0
            log(f"[INFO] 分析 {ticker}.{ex} (size=${size:,.0f})...")

            # 成本控制：非 CH 且 < $10M 跳过 FinanceMCP
            skip_technical = size < STOCK_DATA_MIN_SIZE_USD and ex != "CH"
            if skip_technical:
                market = {"name": name, "price": None, "mcp_technical": None}
                log(f"[INFO] {ticker}.{ex} size < ${STOCK_DATA_MIN_SIZE_USD:,.0f} 跳过 FinanceMCP 技术抓取")
            else:
                market = get_stock_data(ticker, ex, fx_table=None)

            # 用户允许行情失败：继续用空 market 跑新闻和分析
            if not market:
                log(f"[WARN] {ticker}.{ex} 实时数据获取失败，继续用新闻/AI 分析")
                market = {"name": name or ticker}

            news_items = news_map.get(name) or news_map.get(ticker) or []
            news_text = format_news_for_prompt(news_items)

            # T8: 股票级 Kimi 专业数据源增强（仅 A 股核心持仓 >= $10M）
            kimi_stock_enrichment = ""
            if ex.upper() == "CH" and size >= STOCK_DATA_MIN_SIZE_USD:
                log(f"[INFO] 调 Kimi 专业数据源股票增强 {ticker}.{ex}...")
                kimi_stock_enrichment = enrich_stock_context(ticker, ex)
                if kimi_stock_enrichment:
                    log(f"[OK] {ticker}.{ex}: Kimi 股票增强已获取")
                else:
                    log(f"[WARN] {ticker}.{ex}: Kimi 股票增强无数据")

            signal = generate_signal(
                h,
                market,
                macro_summary=macro_summary,
                news_text=news_text,
                kimi_enrichment=kimi_stock_enrichment,
            )
            sections.append(_section(f"  {name} ({ticker}.{ex})"))
            for line in (signal or "🟡 观察").split("\n"):
                if line.strip():
                    sections.append(_section(f"  {line}"))
            sections.append(_section(""))
            time.sleep(0.5)

    sections.append(_section("*数据源: 持仓Excel + 东方财富(CH) + FinanceMCP + Kimi专业数据源*"))

    return {
        "title": title,
        "sections": sections,
        "summary": "\n".join(s[0]["text"] for s in sections if s),
        "stats": {
            "n_holdings": len(active_holdings),
        },
    }


def save_briefing_to_disk(briefing: Dict[str, Any], out_dir: str = BRIEFING_OUTPUT_DIR) -> Optional[str]:
    """把简报存成 JSON (机器读) + Markdown (人读) 到指定目录, 文件名带日期。
    前端静态站可以直接 fetch /briefings/2026-06-05.json。
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        log(f"[ERROR] 创建简报目录失败 {out_dir}: {e}")
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    json_path = os.path.join(out_dir, f"{today}.json")
    md_path = os.path.join(out_dir, f"{today}.md")
    # JSON: 完整结构化 (供前端消费)
    payload = {
        "date": today,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "title": briefing["title"],
        "sections": briefing["sections"],
        "summary": briefing["summary"],
        "stats": briefing.get("stats", {}),
    }
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"[OK] 简报 JSON 写入 {json_path}")
    except Exception as e:
        log(f"[ERROR] 写 JSON 失败 {json_path}: {e}")
        return None
    # Markdown: 人读 (人翻历史简报方便)
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {briefing['title']}\n\n")
            for sec in briefing["sections"]:
                for item in sec:
                    text = item.get("text", "")
                    if text:
                        f.write(text + "\n")
                f.write("\n")
        log(f"[OK] 简报 Markdown 写入 {md_path}")
    except Exception as e:
        log(f"[WARN] 写 Markdown 失败 {md_path}: {e} (非致命)")
    return json_path


def main() -> int:
    """主入口。返回 0=成功 1=失败。"""
    try:
        briefing = generate_briefing()
        ok = send_feishu(briefing["title"], briefing["sections"])
        # T6-user-req: 同时存到本地, 供前端 / 历史查阅
        saved = save_briefing_to_disk(briefing)
        if ok:
            log(f"[DONE] 简报任务完成 (飞书推送 OK, 本地存档: {saved or 'FAILED'})")
            return 0
        log(f"[FAIL] 飞书推送失败 (本地存档: {saved or 'FAILED'})")
        return 1
    except Exception as e:
        log(f"[FATAL] 主流程异常: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
