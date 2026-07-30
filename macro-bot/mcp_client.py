"""
mcp_client.py — FinanceMCP HTTP 客户端封装（T4 任务）

公开 API:
- finance_mcp_call(tool_name, args) -> Optional[Dict]
    通用 JSON-RPC 调用 finance_mcp /tools/call。
- to_ts_code(ticker, exchange) -> Tuple[str, str]
    将 (ticker, exchange) 转换为 Tushare (ts_code, market_type)。
- get_macro_summary(months=3, max_chars=200) -> Optional[str]
    宏观摘要：cpi + ppi + cn_pmi，近 3 个月，< 200 字。
- get_technical_signals(ticker, exchange, days=30) -> Optional[str]
    技术指标摘要：macd(12,26,9) + rsi(14)，近 30 个交易日。
- get_recent_news(short_name, limit=3) -> Optional[List[str]]
    最近 N 条新闻标题（基于 ticker 中文/英文简称搜索）。

设计原则：
1. 失败优雅：所有 MCP 调用返回 None / [] 时上层不崩，只打 log warning
2. 节流防限流：相邻调用间隔 CALL_INTERVAL_SEC（默认 5s），避免 Tushare 限流
3. 会话复用：模块级 session_id，多次调用只 initialize 一次
4. 凭证外移：Tushare token 走 TUSHARE_TOKEN env，HTTP 头 X-Tushare-Token 传递
5. 单元测试：tests/test_mcp_client.py 用 monkeypatch 替换 _post_json RPC 层

环境变量：
- MCP_HTTP_URL            MCP HTTP 端点（默认 http://localhost:3000/mcp）
- MCP_TIMEOUT             单次调用超时秒数（默认 15）
- TUSHARE_TOKEN           Tushare API token（透传给 MCP 服务）
- MCP_CALL_INTERVAL       调用间隔秒数（默认 5）
- MCP_NEWS_MAX_HOLDINGS   抓新闻的持仓上限（默认 5，前 N 大）
- MCP_STOCK_MIN_USD       抓技术指标的最小 USD mkt cap（默认 10_000_000）

历史：
- T1 (bot-rewrite) 在 get_stock_data() 留 TODO/T4 给 FinanceMCP 接入
- T2 (financemcp-deploy) 部署 FinanceMCP HTTP 服务（端口 3000，路径 /mcp）
- T3 (model-router) 路由 Kimi 模型选择
- T4（本任务）= mcp_client.py + bot.py 改造
"""

import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# 加载 .env（生产环境 + 测试环境都需要）
load_dotenv()

# ===== 常量（默认值；运行时通过 _get_* 函数读 env 拿最新值） =====
DEFAULT_MCP_HTTP_URL: str = "http://localhost:3000/mcp"
DEFAULT_MCP_TIMEOUT: int = 15
DEFAULT_CALL_INTERVAL_SEC: float = 5.0
DEFAULT_NEWS_MAX_HOLDINGS: int = 5
DEFAULT_STOCK_DATA_MIN_SIZE_USD: float = 10_000_000.0

# 为了向后兼容（import mcp_client.MCP_HTTP_URL 等仍可用），保留模块级常量
# 但运行时改用 helper 读最新 env（让 .env 改动即时生效 / 单元测试可 monkeypatch）
MCP_HTTP_URL: str = os.getenv("MCP_HTTP_URL", DEFAULT_MCP_HTTP_URL)
MCP_TIMEOUT: int = int(os.getenv("MCP_TIMEOUT", str(DEFAULT_MCP_TIMEOUT)))
TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")
CALL_INTERVAL_SEC: float = float(os.getenv("MCP_CALL_INTERVAL", str(DEFAULT_CALL_INTERVAL_SEC)))
NEWS_MAX_HOLDINGS: int = int(os.getenv("MCP_NEWS_MAX_HOLDINGS", str(DEFAULT_NEWS_MAX_HOLDINGS)))
STOCK_DATA_MIN_SIZE_USD: float = float(
    os.getenv("MCP_STOCK_MIN_USD", str(DEFAULT_STOCK_DATA_MIN_SIZE_USD))
)


def _get_mcp_url() -> str:
    return os.getenv("MCP_HTTP_URL", DEFAULT_MCP_HTTP_URL)


def _get_timeout() -> int:
    return int(os.getenv("MCP_TIMEOUT", str(DEFAULT_MCP_TIMEOUT)))


def _get_tushare_token() -> str:
    return os.getenv("TUSHARE_TOKEN", "")


def _get_call_interval() -> float:
    return float(os.getenv("MCP_CALL_INTERVAL", str(DEFAULT_CALL_INTERVAL_SEC)))


# ===== 模块级状态 =====
_session_id: Optional[str] = None  # MCP 会话 ID（init 一次复用）
_last_call_ts: float = 0.0         # 上次调用时间戳（节流用）


# ===== 内部工具 =====
def _log(msg: str) -> None:
    """统一日志入口。控制台输出。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def _now_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


def _throttle() -> None:
    """距离上次调用 < 当前 env 的 CALL_INTERVAL_SEC 就 sleep 到间隔。"""
    global _last_call_ts
    interval = _get_call_interval()
    now = time.time()
    delta = now - _last_call_ts
    if delta < interval and _last_call_ts > 0:
        time.sleep(interval - delta)
    _last_call_ts = time.time()


def _reset_session() -> None:
    """重置 session + 节流（测试 / 手动维护用）。"""
    global _session_id, _last_call_ts
    _session_id = None
    _last_call_ts = 0.0


def _build_headers() -> Dict[str, str]:
    """构造 HTTP 头。Tushare token 走 X-Tushare-Token（与 httpServer.ts 一致）。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    token = _get_tushare_token()
    if token:
        headers["X-Tushare-Token"] = token
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    return headers


def _init_session() -> None:
    """调用 MCP initialize 获取 session id。失败不回 panic。"""
    global _session_id
    if _session_id is not None:
        return
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "macro-bot", "version": "1.0.0"},
        },
    }
    try:
        resp = requests.post(
            _get_mcp_url(),
            json=body,
            headers=_build_headers(),
            timeout=_get_timeout(),
        )
        if resp.status_code == 200:
            sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
            if sid:
                _session_id = sid
            else:
                # 服务器没返回 session id，tools/call 也能工作（无状态）
                pass
    except Exception as e:
        # initialize 失败不致命，tools/call 不强依赖 session
        _log(f"[WARN] MCP initialize failed: {e}")


# ===== 通用 RPC =====
def finance_mcp_call(
    tool_name: str,
    args: Dict[str, Any],
    *,
    _no_throttle: bool = False,
) -> Optional[Dict[str, Any]]:
    """通用 FinanceMCP JSON-RPC 调用。

    Args:
        tool_name: 工具名（macro_econ / stock_data / finance_news / ...）
        args: 工具参数 dict
        _no_throttle: 测试用，跳过节流

    Returns:
        MCP 返回的 result dict（通常含 content 数组），或 None
    """
    if not _no_throttle:
        _throttle()

    # 懒加载 session
    if _session_id is None:
        _init_session()

    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    try:
        resp = requests.post(
            _get_mcp_url(),
            json=body,
            headers=_build_headers(),
            timeout=_get_timeout(),
        )
    except requests.Timeout:
        _log(f"[WARN] MCP {tool_name} timeout after {_get_timeout()}s")
        return None
    except requests.ConnectionError as e:
        _log(f"[WARN] MCP {tool_name} connection error: {e}")
        return None
    except Exception as e:
        _log(f"[ERROR] MCP {tool_name} unexpected: {e}")
        return None

    if resp.status_code != 200:
        _log(f"[WARN] MCP {tool_name} HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except ValueError as e:
        _log(f"[WARN] MCP {tool_name} non-JSON response: {e}")
        return None

    if not isinstance(data, dict):
        return None
    if "error" in data and data["error"]:
        err = data["error"]
        _log(f"[WARN] MCP {tool_name} RPC error: {err}")
        return None
    return data.get("result")


def _extract_text(result: Dict[str, Any]) -> str:
    """从 MCP content 数组提取 text 字段拼接为字符串。"""
    content = result.get("content")
    if not content or not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


# ===== ts_code / market_type 转换器 =====
def to_ts_code(ticker: str, exchange: str) -> Tuple[str, str]:
    """将 (ticker, exchange) 转换为 Tushare 用的 (ts_code, market_type)。

    转换规则（与 task spec + dispatch.ts 参数约束一致）:
        CH  → (688169.SH, cn)        # Tushare 用 .SH 不是 .SS
        HK  → (09992.HK, hk)         # 补零到 5 位
        US  → (LKNCY, us)            # 透传
        JP  → (9843.T, jp)
        GR  → (PUM.DE, de)           # Germany Xetra
        TT  → (2330.TW, tw)          # Taiwan
        KS  → (005930.KS, kr)        # Korea
        NZ  → (XYZ.NZ, nz)
        其他 → (ticker, "other")

    Args:
        ticker: Excel 中的 ticker（不含后缀）
        exchange: 市场代码（CH/HK/US/JP/GR/TT/KS/NZ...）

    Returns:
        (ts_code, market_type)
    """
    t = str(ticker).strip()
    ex = (exchange or "").upper().strip()
    if ex == "CH":
        suffix = ".SH" if t.startswith(("6", "9")) else ".SZ"
        return f"{t}{suffix}", "cn"
    if ex == "HK":
        return f"{t.zfill(5)}.HK", "hk"
    if ex == "US":
        return t, "us"
    if ex == "JP":
        return f"{t}.T", "jp"
    if ex == "GR":
        return f"{t}.DE", "de"
    if ex == "TT":
        return f"{t}.TW", "tw"
    if ex == "KS":
        return f"{t}.KS", "kr"
    if ex == "NZ":
        return f"{t}.NZ", "nz"
    return t, "other"


# ===== Helper 1: 宏观摘要 =====
def get_macro_summary(
    months: int = 3,
    max_chars: int = 200,
    indicators: Tuple[str, ...] = ("cpi", "ppi", "cn_pmi"),
) -> Optional[str]:
    """生成宏观摘要：依次调 macro_econ(cpi/ppi/cn_pmi)，近 N 个月。

    调用次数 = len(indicators)，共享给所有持仓。3 个指标 × 5s = 15s 节流。

    Args:
        months: 向前回看月数（默认 3）
        max_chars: 返回字符串最大字符数（默认 200，task spec 约束）
        indicators: 指标列表

    Returns:
        形如 "CPI=... | PPI=... | CN_PMI=..." 的字符串；失败返回 None
    """
    end = _now_yyyymmdd()
    start_dt = datetime.now() - timedelta(days=months * 31)
    start = start_dt.strftime("%Y%m%d")

    snippets: List[str] = []
    for indicator in indicators:
        result = finance_mcp_call(
            "macro_econ",
            {"indicator": indicator, "start_date": start, "end_date": end},
        )
        if not result:
            snippets.append(f"{indicator.upper()}=N/A")
            continue
        text = _extract_text(result)
        first_line = text.split("\n", 1)[0].strip() if text else ""
        if not first_line or first_line.startswith("未找到") or "失败" in first_line:
            first_line = "N/A"
        if len(first_line) > 80:
            first_line = first_line[:80] + "..."
        snippets.append(f"{indicator.upper()}={first_line}")

    combined = " | ".join(snippets)
    if len(combined) > max_chars:
        combined = combined[: max_chars - 3] + "..."
    return combined if combined else None


# ===== Helper 2: 技术指标 =====
def get_technical_signals(
    ticker: str,
    exchange: str,
    days: int = 30,
    indicators: str = "macd(12,26,9) rsi(14)",
    *,
    max_chars: int = 800,
) -> Optional[str]:
    """获取单只持仓的技术指标摘要。

    调用 stock_data 一次。task spec: macd(12,26,9) + rsi(14)，近 30 天。

    Args:
        ticker: Excel ticker
        exchange: 市场代码
        days: 回看天数
        indicators: 指标字符串（默认 macd(12,26,9) rsi(14)）
        max_chars: 返回字符串最大字符数（裁剪避免 prompt 过长）

    Returns:
        摘要字符串（行情 + 技术指标），或 None
    """
    ts_code, market_type = to_ts_code(ticker, exchange)
    end = _now_yyyymmdd()
    start = _days_ago(days)
    result = finance_mcp_call(
        "stock_data",
        {
            "code": ts_code,
            "market_type": market_type,
            "start_date": start,
            "end_date": end,
            "indicators": indicators,
        },
    )
    if not result:
        return None
    text = _extract_text(result)
    if not text:
        return None
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


# ===== Helper 3: 新闻 =====
def get_recent_news(
    short_name: str,
    limit: int = 3,
) -> Optional[List[str]]:
    """获取指定短名的最近 N 条新闻标题。

    调 finance_news 一次。返回标题列表（不含摘要/来源/时间）。
    文本解析：服务返回 `标题\n来源: ... 时间: ...\n摘要: ...\n---\n下一条...`，
    我们只保留标题行。

    Args:
        short_name: 股票简称（如"苹果"/"APPLE"/"AAPL"）
        limit: 返回标题数（默认 3）

    Returns:
        标题列表；无结果返回 None
    """
    query = (short_name or "").strip()
    if not query:
        return None
    result = finance_mcp_call("finance_news", {"query": query})
    if not result:
        return None
    text = _extract_text(result)
    if not text:
        return None
    # 跳过纯信息行（注意：空串 "" 会让 startswith 永远为 True，单独处理）
    skip_prefixes = ("#", "未找到", "搜索", "来源", "摘要", "链接", "时间", "---")
    titles: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:  # 空行单独 skip（避免 startswith('') 永远 True 的坑）
            continue
        if any(line.startswith(p) for p in skip_prefixes):
            continue
        # 服务返回的第一行是 "# query 财经新闻搜索结果" — 上面已 skip
        # 之后每段以 "标题\\n来源: ..." 形式排列
        titles.append(line)
        if len(titles) >= limit:
            break
    return titles if titles else None
