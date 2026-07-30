"""
增强版宏观早报：把 stock-tweet-bot 抓取的 X 推文作为独立数据源接入，
与原有 news_fetcher / FinanceMCP / AKShare 数据做综合分析与归因。

运行：
    cd /www/wwwroot/macro-bot
    python3 bot_tweet_enhanced.py

依赖：
    复用 bot.py / news_fetcher.py / model_router.py / holdings.py / mcp_client.py
"""

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# 复用已有模块
sys.path.insert(0, BASE_DIR)
from bot import (
    BRIEFING_OUTPUT_DIR,
    CAT_NAMES,
    FEISHU_SIGN_SECRET,
    FEISHU_WEBHOOK,
    _feishu_sign,
    _get_macro_summary_with_fallback,
    get_stock_data,
    load_holdings,
    log,
    send_feishu,
)
from model_router import call_kimi_with_router
from news_fetcher import fetch_news_for_holding, format_news_for_prompt

TWEETS_FILE = "/www/wwwroot/stock-tweet-bot/tweets.jsonl"


# ===== 推文读取与聚合 =====
def load_tweets(path: str = TWEETS_FILE, max_age_hours: int = 48) -> List[Dict[str, Any]]:
    """读取 stock-tweet-bot 输出的 tweets.jsonl，每行是一个 xAI 生成的叙事总结对象。"""
    if not os.path.exists(path):
        log(f"[WARN] 推文文件不存在: {path}")
        return []
    tweets: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            if not isinstance(t, dict):
                continue
            # 时间过滤
            ts = t.get("_fetch_time_utc")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    if (now - dt).total_seconds() > max_age_hours * 3600:
                        continue
                except Exception:
                    pass
            # 过滤明显无信息的总结
            narrative = (t.get("narrative") or "").strip()
            if not narrative and not t.get("key_points") and not t.get("sources"):
                continue
            tweets.append(t)
    log(f"[INFO] 加载 {len(tweets)} 条叙事总结（来源: {path}）")
    return tweets


def _ticker_key(ticker: str) -> str:
    return str(ticker).upper().split(".")[0]


def group_tweets_by_ticker(tweets: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in tweets:
        key = _ticker_key(t.get("ticker", ""))
        if key:
            groups[key].append(t)
    # 按 engagement 排序
    for key in groups:
        groups[key].sort(key=lambda x: x.get("engagement_score", 0), reverse=True)
    return groups


def format_tweets_for_prompt(tweets: List[Dict[str, Any]], max_tweets: int = 5) -> str:
    """把 xAI 生成的叙事总结对象格式化成 prompt 文本。"""
    if not tweets:
        return ""
    t = tweets[0]  # 每 ticker 一个 summary
    lines = ["【X/Twitter 市场叙事总结（过去24h，由 xAI 基于真实推文提炼）】"]
    lines.append(f"叙事: {t.get('narrative', '')}")
    key_points = t.get("key_points") or []
    if key_points:
        lines.append("要点:")
        for i, kp in enumerate(key_points[:max_tweets], 1):
            lines.append(f"  {i}. {kp}")
    sources = t.get("sources") or []
    if sources:
        lines.append("关键来源:")
        for i, s in enumerate(sources[:3], 1):
            url = s.get("url") or s.get("link", "")
            summary = s.get("summary", "")
            author = s.get("author", "")
            rel = s.get("relevance", "")
            lines.append(f"  {i}. {author} ({rel}): {summary} [{url}]")
    lines.append(f"市场相关性: {t.get('market_relevance', 'unknown')} | 投资相关: {t.get('is_investment_related', False)}")
    lines.append(f"价格走势假设: {t.get('price_movement_hypothesis', 'N/A')}")
    return "\n".join(lines)


def tweet_narrative_summary(ticker: str, tweets: List[Dict[str, Any]], max_samples: int = 5) -> str:
    """展示 xAI 对该 ticker 的 X 叙事总结，包含关键来源链接。"""
    if not tweets:
        return ""
    t = tweets[0]
    name = t.get("display_name") or ticker
    relevance = t.get("market_relevance", "unknown")
    inv_rel = t.get("is_investment_related", False)
    lines = [f"📌 {name}({ticker}) | X 相关性: {relevance} | 投资相关: {inv_rel}"]
    lines.append(f"叙事: {t.get('narrative', '')}")
    key_points = t.get("key_points") or []
    if key_points:
        lines.append("要点:")
        for i, kp in enumerate(key_points[:max_samples], 1):
            lines.append(f"  {i}. {kp}")
    sources = t.get("sources") or []
    if sources:
        lines.append("关键来源:")
        for i, s in enumerate(sources[:3], 1):
            url = s.get("url") or s.get("link", "")
            summary = s.get("summary", "")
            author = s.get("author", "")
            rel = s.get("relevance", "")
            lines.append(f"  {i}. {author} ({rel}): {summary}")
            if url:
                lines.append(f"     🔗 {url}")
    lines.append(f"价格走势假设: {t.get('price_movement_hypothesis', 'N/A')}")
    return "\n".join(lines)




def _build_user_prompt(holding, market, macro_summary=None, news_text=None, tweets_text=None, tweet_summary=None):
    """Construct Kimi user prompt (T5 enhanced with structured news text + X narrative)."""
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

    if md.get("mcp_technical"):
        lines.append("")
        lines.append("【技术指标（FinanceMCP / 近 30 日）】")
        lines.append(md["mcp_technical"])

    if macro_summary:
        lines.append("")
        lines.append("【宏观摘要（近 3 月）】")
        lines.append(macro_summary)

    if news_text:
        lines.append("")
        lines.append("【新闻/事件】")
        lines.append("请严格区分以下两类：\n· 【今日焦点（过去24小时）】必须作为主体分析；\n· 【近30日背景】仅作为补充背景，不准展开分析。")
        lines.append("")
        lines.append(news_text)

    if tweets_text:
        lines.append("")
        lines.append(tweets_text)

    if tweet_summary:
        lines.append("")
        lines.append(tweet_summary)

    return "\n".join(lines)


def generate_enhanced_signal(
    holding,
    market=None,
    signal_strength="base",
    macro_summary=None,
    news_text=None,
    tweets_text=None,
    tweet_summary=None,
):
    """Generate AI signal for single holding via model_router, with X narrative."""
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
        "- 若 prompt 中包含【X/Twitter 市场叙事总结】：\n"
        "   · 这是 xAI 基于 X 上相关推文提炼的叙事总结，不是原始 tweet 堆砌；\n"
        "   · 分析 X 上讨论的核心论点是什么（包括投资/品牌/事件驱动叙事，如 BTS 代言 Gucci 这类可能带动销售的事件）；\n"
        "   · 判断这些论点是否已被传统媒体/公司公告覆盖，是否构成新的信息增量，还是只是情绪放大；\n"
        "   · 结合当日股价涨跌幅，判断该叙事是否可能解释了股价变动（例如品牌/事件叙事驱动短期情绪，但需看股价是否已反应）；\n"
        "   · 参考 xAI 给出的 market_relevance 和 price_movement_hypothesis，但不要原样复述，要形成你自己的判断；\n"
        "   · 最终给出定性判断：确认现有观点、提供早期预警、还是噪音。\n"
    )

    user_prompt = _build_user_prompt(holding, market, macro_summary, news_text, tweets_text, tweet_summary) + (
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



# ===== 生成增强版简报 =====
def generate_enhanced_briefing(holdings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if holdings is None:
        holdings = load_holdings(file_path=None, log=log) or []

    active = [h for h in holdings if h.get("run", True)]
    log(f"[INFO] 增强简报：Run=Y 持仓 {len(active)} 条")

    # 宏观摘要
    macro_summary = _get_macro_summary_with_fallback(months=3, max_chars=200)

    # 加载推文
    tweets = load_tweets()
    tweet_groups = group_tweets_by_ticker(tweets)

    title = f"📊 宏观早报（X舆情增强版）[{datetime.now().strftime('%Y-%m-%d')}]"
    sections: List[List[Dict[str, str]]] = []
    sections.append(_section(f"生成时间: {datetime.now().strftime('%H:%M')}"))
    sections.append(_section(f"分析总数: {len(active)}只 | X推文数据源: {len(tweets)}条"))
    sections.append(_section(""))

    if macro_summary:
        sections.append(_section("【宏观摘要（近 3 月）】"))
        sections.append(_section(macro_summary))
        sections.append(_section(""))

    # 先出 top 舆情总览（只显示高/中相关性的叙事，且展示完整总结）
    if tweets:
        sections.append(_section("【X 市场叙事速览】"))
        ranked = sorted(
            tweet_groups.items(),
            key=lambda kv: ({"high": 3, "medium": 2, "low": 1, "none": 0, "unknown": 0}.get(kv[1][0].get("market_relevance", "unknown").lower(), 0), len(kv[1])),
            reverse=True,
        )
        for ticker_key, tws in ranked[:5]:
            summary = tweet_narrative_summary(ticker_key, tws, max_samples=3)
            if summary:
                for line in summary.split("\n"):
                    if line.strip():
                        sections.append(_section(line))
                sections.append(_section(""))
        sections.append(_section(""))

    sections.append(_section("【持仓信号】"))
    sections.append(_section(""))

    # 按 category 分组
    cats: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for h in active:
        cats.setdefault(h.get("category", 0), []).append(h)

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

            market = get_stock_data(ticker, ex, fx_table=None)
            if not market:
                sections.append(_section(f"  {name}({ticker}.{ex}): 实时数据获取失败"))
                sections.append(_section(""))
                continue

            # 原有新闻
            news_items = fetch_news_for_holding(h, hours_window=24, max_results=5)
            news_text = format_news_for_prompt(news_items)

            # 推文
            tws = tweet_groups.get(_ticker_key(ticker), [])
            tweets_text = format_tweets_for_prompt(tws, max_tweets=5)

            tweets_text_local = tweets_text
            tweet_summary_local = tweet_narrative_summary(ticker, tws)
            signal = generate_enhanced_signal(
                h, market, macro_summary=macro_summary,
                news_text=news_text,
                tweets_text=tweets_text_local,
                tweet_summary=tweet_summary_local,
            )
            sections.append(_section(f"  {name} ({ticker}.{ex})"))
            for line in (signal or "🟡 观察").split("\n"):
                if line.strip():
                    sections.append(_section(f"  {line}"))
            sections.append(_section(""))
            time.sleep(0.3)

    sections.append(_section("*数据源: 持仓Excel + 东方财富(CH) + FinanceMCP + Kimi + X/Grok*"))

    return {
        "title": title,
        "sections": sections,
        "summary": "\n".join(s[0]["text"] for s in sections if s),
        "stats": {"n_holdings": len(active), "n_tweets": len(tweets)},
    }


def _section(text: str) -> List[Dict[str, str]]:
    return [{"tag": "text", "text": text}]


def save_enhanced_briefing(briefing: Dict[str, Any], out_dir: str = BRIEFING_OUTPUT_DIR) -> Optional[str]:
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        log(f"[ERROR] 创建简报目录失败 {out_dir}: {e}")
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    json_path = os.path.join(out_dir, f"{today}.tweets.json")
    md_path = os.path.join(out_dir, f"{today}.tweets.md")
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
        log(f"[OK] 增强简报 JSON 写入 {json_path}")
    except Exception as e:
        log(f"[ERROR] 写 JSON 失败 {json_path}: {e}")
        return None
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {briefing['title']}\n\n")
            for sec in briefing["sections"]:
                for item in sec:
                    text = item.get("text", "")
                    if text:
                        f.write(text + "\n")
                f.write("\n")
        log(f"[OK] 增强简报 Markdown 写入 {md_path}")
    except Exception as e:
        log(f"[WARN] 写 Markdown 失败 {md_path}: {e}")
    return json_path


def main(push_feishu: bool = False) -> int:
    try:
        briefing = generate_enhanced_briefing()
        if push_feishu:
            send_feishu(briefing["title"], briefing["sections"])
        saved = save_enhanced_briefing(briefing)
        log(f"[DONE] 增强简报任务完成 (本地存档: {saved or 'FAILED'})")
        return 0
    except Exception as e:
        log(f"[FATAL] 增强简报异常: {e}")
        import traceback
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    push = os.getenv("PUSH_FEISHU", "false").lower() == "true"
    raise SystemExit(main(push_feishu=push))