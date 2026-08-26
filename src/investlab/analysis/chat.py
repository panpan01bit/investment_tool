"""对话追问：基于简报/报告上下文 + 联网搜索的问答（替代旧 /api/chat）。"""

from __future__ import annotations

from ..config import get_settings
from ..llm.client import get_llm
from ..search import format_hits_context, search
from ..utils.common import setup_logging, today_str
from .briefing import load_briefing

log = setup_logging("investlab.chat")

CHAT_PROMPT = """你是投资研究助理。结合给定上下文回答用户问题。
规则：
- 优先使用「当日简报数据」与「搜索结果」中的事实，引用数字；
- 搜索得到的网页结论标注来源域名（如 [caixin.com]）；
- 简报与搜索都没有的信息，明确说“本地数据中没有”，可给方法论但禁止编造事实；
- 涉及操作建议时补一句“非投资建议”。
日期：{date}

当日简报数据：
<briefing>
{briefing}
</briefing>

搜索结果：
<web>
{web}
</web>"""


def chat(question: str, *, use_search: bool = True) -> dict:
    q = (question or "").strip()
    if not q:
        return {"answer": "问题为空", "sources": []}
    settings = get_settings()
    briefing = load_briefing()
    web_text = ""
    hits = []
    if use_search:
        try:
            hits = search(q, max_results=settings.search_max_results)
            web_text = format_hits_context(hits)
        except Exception as exc:
            log.debug("搜索失败: %s", exc)

    llm = get_llm()
    if llm is None:
        # 无 LLM：直接返回检索到的材料
        if not hits and not briefing:
            return {"answer": "未配置 LLM 且无可用检索结果。请配置 INVESTLAB_LLM_API_KEY 后重试。",
                    "sources": []}
        lines = ["（未配置 LLM，以下为原始检索材料）", ""]
        lines.append(format_hits_context(hits) or "(无)")
        return {"answer": "\n".join(lines),
                "sources": [{"title": h["title"], "url": h["url"]} for h in hits[:6]]}

    system = CHAT_PROMPT.replace("{date}", today_str()).replace(
        "{briefing}", _compact(briefing)[:2400]
    ).replace("{web}", web_text[:2000])
    resp = llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": q}],
        model=settings.llm_model,
        temperature=0.4,
        max_tokens=1600,
    )
    return {
        "answer": resp.text.strip(),
        "model": resp.model,
        "sources": [
            {"title": h["title"], "url": h["url"], "domain": _dom(h["url"])}
            for h in hits[:6]
        ],
    }


def chat_sync_or_degrade(question: str):
    from ..llm.client import LLMError

    try:
        return chat(question)
    except LLMError as exc:
        return {"answer": f"LLM 调用失败：{exc}", "sources": []}


def _compact(briefing: dict | None) -> str:
    import json

    if not briefing:
        return "(今日简报尚未生成)"
    keep = {}
    for k in ("date", "positions", "macro"):
        v = briefing.get(k)
        if v is not None:
            keep[k] = v
    news = briefing.get("news") or {}
    fresh = news.get("fresh") or []
    if fresh:
        keep["news_headlines"] = [a.get("title") for a in fresh[:8]]
    return json.dumps(keep, ensure_ascii=False, default=str)


def _dom(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""
