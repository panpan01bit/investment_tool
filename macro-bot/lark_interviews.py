# -*- coding: utf-8 -*-
"""
lark_interviews.py — 专家数据库（飞书多维表格）接口

环境变量：
  LARK_APP_ID
  LARK_APP_SECRET
  LARK_BASE_TOKEN
  LARK_TABLE_ID

依赖：
  requests（已在 requirements.txt 中）
"""
from __future__ import print_function

import json
import os
import re
import time
from datetime import datetime

import requests

LARK_APP_ID = os.getenv("LARK_APP_ID", "").strip()
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", "").strip()
LARK_BASE_TOKEN = os.getenv("LARK_BASE_TOKEN", "").strip()
LARK_TABLE_ID = os.getenv("LARK_TABLE_ID", "").strip()

FEISHU_HOST = "https://open.feishu.cn"

_token_cache = {"token": None, "expires_at": 0.0}


_FIELD_MAP = {
    "文件名": "file_name",
    "行业": "industry",
    "访谈日期": "interview_date",
    "音频转写（预留）": "audio_transcription",
    "专家姓名": "expert_name",
    "专家公司": "expert_company",
    "专家职位": "expert_position",
    "专家履历": "expert_bio",
    "涉及公司": "related_companies",
    "Ticker": "ticker",
    "纪要类型": "memo_type",
    "要点": "key_points",
    "关键数据": "key_data",
    "情绪判断": "sentiment",
    "情绪说明": "sentiment_note",
    "标签": "tags",
    "原文": "original_text",
    "源文件": "source_files",
    "Parent items": "parent_items",
}

_INDUSTRY_OPTIONS = [
    "零售", "工具", "家具", "家电", "房地产", "汽车", "半导体", "其他",
    "消费电子", "家电/智能硬件", "家用电器",
]
_MEMO_TYPE_OPTIONS = ["专家访谈", "业绩纪要", "行业研判", "纪要", "数据反推"]
_SENTIMENT_OPTIONS = ["正面", "中性", "负面", "中性偏好", "谨慎"]
_TAG_OPTIONS = [
    "关税", "库存", "促销", "竞争", "房地产", "订单", "扩产", "政策",
    "智能驾驶", "国产替代", "出海", "追觅", "IPO", "国资", "创始人", "智能硬件",
]


def _config_ok():
    return all([LARK_APP_ID, LARK_APP_SECRET, LARK_BASE_TOKEN, LARK_TABLE_ID])


def _tenant_access_token():
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] - now > 60:
        return _token_cache["token"]
    url = f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError("Feishu auth error: %s" % data)
    token = data["tenant_access_token"]
    expire = data.get("expire", 7200)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expire
    return token


def _headers():
    return {
        "Authorization": "Bearer %s" % _tenant_access_token(),
        "Content-Type": "application/json",
    }


def _records_url():
    return f"{FEISHU_HOST}/open-apis/bitable/v1/apps/{LARK_BASE_TOKEN}/tables/{LARK_TABLE_ID}/records"


def _closest_option(value, options):
    if not value:
        return ""
    v = str(value).strip()
    if v in options:
        return v
    # 模糊匹配：包含关系
    for opt in options:
        if opt in v or v in opt:
            return opt
    return ""


def _record_to_dict(record):
    """把飞书记录字段名映射成前端用的英文 key。"""
    fields = record.get("fields", {}) or {}
    out = {"id": record.get("record_id") or record.get("id")}
    for cn, en in _FIELD_MAP.items():
        val = fields.get(cn)
        if cn == "访谈日期" and val:
            # 飞书返回的 DateTime 可能是时间戳(ms)或字符串
            if isinstance(val, (int, float)):
                try:
                    val = datetime.fromtimestamp(val / 1000.0).strftime("%Y-%m-%d")
                except Exception:
                    val = str(val)
            else:
                val = str(val)
        out[en] = val
    return out


def list_interviews(limit=500):
    if not _config_ok():
        raise RuntimeError("Missing LARK_APP_ID / LARK_APP_SECRET / LARK_BASE_TOKEN / LARK_TABLE_ID")
    url = _records_url()
    records = []
    page_token = None
    while len(records) < limit:
        params = {"page_size": min(500, limit - len(records))}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError("Feishu list records error: %s" % data)
        items = data["data"].get("items", [])
        records.extend(items)
        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
        if not items:
            break
    return records


def get_interviews(limit=500):
    records = list_interviews(limit)
    return [_record_to_dict(r) for r in records]


def create_record(fields):
    if not _config_ok():
        raise RuntimeError("Missing LARK_APP_ID / LARK_APP_SECRET / LARK_BASE_TOKEN / LARK_TABLE_ID")
    url = _records_url()
    resp = requests.post(url, headers=_headers(), json={"fields": fields}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError("Feishu create record error: %s" % data)
    return data


def _call_kimi(messages, model=None, max_tokens=2500, timeout=120):
    api_key = os.getenv("KIMI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("KIMI_API_KEY not set")
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/")
    model = model or os.getenv("KIMI_BASE_MODEL", "kimi-k2.5")
    headers = {
        "Authorization": "Bearer %s" % api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    # kimi-k2.5 非思考模式可接受 temperature；其他模型 omit 更安全
    if not model.endswith("-thinking"):
        payload["temperature"] = 0.3
    resp = requests.post(
        "%s/chat/completions" % base_url,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _strip_json_markdown(text):
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def parse_interview(content, title):
    """
    解析访谈内容，调用 Kimi 提取结构化信息，并写入飞书多维表格。
    返回前端期望的格式：
      {success, total, inserted, docTitle, expertName, opinions, error}
    """
    if not _config_ok():
        return {
            "success": False,
            "total": 0,
            "inserted": 0,
            "docTitle": title,
            "expertName": "",
            "opinions": [],
            "error": "Missing LARK_* environment variables",
        }
    if not content or not title:
        return {
            "success": False,
            "total": 0,
            "inserted": 0,
            "docTitle": title or "",
            "expertName": "",
            "opinions": [],
            "error": "content and title are required",
        }

    system_prompt = """你是一位金融研究员助手，负责从访谈/纪要文本中提取结构化信息。
请只返回一个 JSON 对象，不要包含 markdown 代码块。JSON 必须符合以下 schema：
{
  "expert_name": "专家姓名",
  "expert_company": "专家所在公司",
  "expert_position": "专家职位",
  "expert_bio": "专家履历（若无请填空字符串）",
  "industry": "行业，必须从以下选项中选取最接近的一个：零售、工具、家具、家电、房地产、汽车、半导体、其他、消费电子、家电/智能硬件、家用电器",
  "related_companies": "涉及的公司名称，用逗号分隔",
  "ticker": "相关股票代码/Ticker，用逗号分隔",
  "interview_date": "访谈日期，格式 YYYY-MM-DD，若未明确请填 null",
  "memo_type": "纪要类型，从以下选项中选取最接近的一个：专家访谈、业绩纪要、行业研判、纪要、数据反推",
  "key_points": "要点，用中文总结核心观点",
  "key_data": "关键数据，提取重要数字/指标",
  "sentiment": "情绪判断，从以下选项中选取最接近的一个：正面、中性、负面、中性偏好、谨慎",
  "sentiment_note": "情绪说明，可填空字符串",
  "tags": ["标签数组，从以下选项中选取零个或多个：关税、库存、促销、竞争、房地产、订单、扩产、政策、智能驾驶、国产替代、出海、追觅、IPO、国资、创始人、智能硬件"],
  "opinions": [
    {
      "topic": "观点主题",
      "summary": "观点摘要",
      "sentiment": "该观点的情绪，从正面、中性、负面、中性偏好、谨慎中选取",
      "evidence": "支撑该观点的数据/证据"
    }
  ]
}
若某字段无信息，请使用空字符串或 null。"""

    user_prompt = "文件名/标题：%s\n\n原文内容：\n%s" % (title, content[:20000])

    try:
        raw = _call_kimi([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
        parsed = json.loads(_strip_json_markdown(raw))
    except Exception as e:
        return {
            "success": False,
            "total": 0,
            "inserted": 0,
            "docTitle": title,
            "expertName": "",
            "opinions": [],
            "error": "LLM parse failed: %s" % e,
        }

    opinions = parsed.get("opinions") or []
    if not isinstance(opinions, list):
        opinions = []
    if not opinions:
        opinions = [{
            "topic": title,
            "summary": parsed.get("key_points", ""),
            "sentiment": parsed.get("sentiment", "中性"),
            "evidence": parsed.get("key_data", ""),
        }]

    interview_date = parsed.get("interview_date")
    if not interview_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(interview_date)):
        interview_date = datetime.now().strftime("%Y-%m-%d")

    base_fields = {
        "文件名": title,
        "专家姓名": parsed.get("expert_name", ""),
        "专家公司": parsed.get("expert_company", ""),
        "专家职位": parsed.get("expert_position", ""),
        "专家履历": parsed.get("expert_bio", ""),
        "行业": _closest_option(parsed.get("industry"), _INDUSTRY_OPTIONS),
        "涉及公司": parsed.get("related_companies", ""),
        "Ticker": parsed.get("ticker", ""),
        "访谈日期": interview_date,
        "纪要类型": _closest_option(parsed.get("memo_type"), _MEMO_TYPE_OPTIONS) or "专家访谈",
        "关键数据": parsed.get("key_data", ""),
        "情绪说明": parsed.get("sentiment_note", ""),
        "标签": [t for t in (parsed.get("tags") or []) if t in _TAG_OPTIONS] or [],
        "原文": content,
    }

    inserted = 0
    errors = []
    for op in opinions:
        fields = dict(base_fields)
        fields["要点"] = op.get("summary", "") or parsed.get("key_points", "")
        fields["关键数据"] = op.get("evidence", "") or parsed.get("key_data", "")
        fields["情绪判断"] = _closest_option(op.get("sentiment"), _SENTIMENT_OPTIONS) or _closest_option(parsed.get("sentiment"), _SENTIMENT_OPTIONS) or "中性"
        try:
            create_record(fields)
            inserted += 1
        except Exception as e:
            errors.append(str(e))

    expert_name = parsed.get("expert_name", "")
    return {
        "success": inserted > 0,
        "total": len(opinions),
        "inserted": inserted,
        "docTitle": title,
        "expertName": expert_name,
        "opinions": opinions,
        "error": "; ".join(errors) if errors else "",
    }
