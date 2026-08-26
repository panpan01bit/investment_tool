"""LLM 客户端：OpenAI 兼容 Chat Completions 协议（默认指向 Kimi/Moonshot）。

- 角色模型分离：fast（摘要/标签）/ think（深度分析）/ vision（图表理解，可选）
- 出站请求统一走 netguard 安全校验
- 用量记录到 data/logs/llm_usage.jsonl
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings, get_settings
from ..netguard import http_post_json
from ..utils.common import now_cn, setup_logging

log = setup_logging("investlab.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict
    elapsed_s: float


class LLMClient:
    """薄封装：不引入 openai SDK，直接走 /chat/completions。"""

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        if not self.s.llm_api_key:
            raise LLMError(
                "未配置 INVESTLAB_LLM_API_KEY（或兼容旧名 KIMI_API_KEY），LLM 功能不可用"
            )

    # ------------------------------------------------------------- 底层调用

    def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_json: bool = False,
        timeout_s: float = 120,
    ) -> LLMResponse:
        url = f"{self.s.llm_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model or self.s.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}
        started = time.time()
        try:
            data = http_post_json(
                url,
                payload,
                headers={"Authorization": f"Bearer {self.s.llm_api_key}"},
                timeout=timeout_s,
                retries=1,
            )
        except Exception as exc:
            raise LLMError(f"LLM 请求失败: {exc}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise LLMError(f"LLM 返回错误: {data['error']}")
        choices = (data or {}).get("choices") or []
        if not choices:
            raise LLMError(f"LLM 响应缺少 choices: {str(data)[:200]}")
        text = choices[0].get("message", {}).get("content", "") or ""
        resp = LLMResponse(
            text=text,
            model=payload["model"],
            usage=data.get("usage") or {},
            elapsed_s=round(time.time() - started, 2),
        )
        self._log_usage(resp)
        return resp

    def _log_usage(self, resp: LLMResponse) -> None:
        log_dir = self.s.logs_dir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": now_cn().isoformat(timespec="seconds"),
                "model": resp.model,
                "usage": resp.usage,
                "elapsed_s": resp.elapsed_s,
            }
            with open(log_dir / "llm_usage.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- 角色 API

    def fast(self, prompt: str, system: str = "", **kw) -> LLMResponse:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        return self.chat(msgs, model=self.s.llm_model, **kw)

    def think(self, prompt: str, system: str = "", **kw) -> LLMResponse:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        return self.chat(msgs, model=self.s.llm_think_model, **kw)

    def vision_image(
        self,
        prompt: str,
        image_path: Path | None = None,
        image_b64: str | None = None,
        mime: str = "image/png",
        **kw,
    ) -> LLMResponse:
        """视觉模型识图（券商报告图表提取用）。"""
        model = self.s.llm_vision_model
        if not model:
            raise LLMError("未配置 INVESTLAB_LLM_VISION_MODEL，跳过视觉能力")
        if image_b64 is None:
            if image_path is None:
                raise LLMError("需要 image_path 或 image_b64")
            image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_b64}"},
            },
        ]
        return self.chat([{"role": "user", "content": content}], model=model, **kw)


def get_llm(settings: Settings | None = None) -> LLMClient | None:
    """返回客户端；未配置 key 时返回 None（让上层优雅降级）。"""
    try:
        return LLMClient(settings)
    except LLMError:
        log.warning("LLM 未配置（缺 API Key），相关功能将降级为纯本地模式")
        return None


def extract_json(text: str):
    """从 LLM 输出中尽力提取 JSON 对象（容忍 ```json 围栏与前后杂讯）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(t[start : end + 1])
    except ValueError:
        return None


def singleton_client() -> LLMClient | None:
    return get_llm()
