"""
model_router.py — 多模型路由器
按持仓金额自动选 Kimi 模型（强 vs 基础），含 fallback、缓存、JSONL 用量埋点。

公开 API：
- select_model(holding) -> (model_name, reason)
- call_kimi_with_router(holding, signal_strength, system_prompt, user_prompt) -> str
- clear_cache()  # 测试/手动清理用

环境变量：
- KIMI_API_KEY        必填
- KIMI_BASE_MODEL     默认 "kimi-k2.5"
- KIMI_STRONG_MODEL   默认 "kimi-k2-thinking"
- KIMI_API_URL        默认 "https://api.moonshot.cn/v1/chat/completions"
- LOG_DIR             用量日志目录，默认 "logs"

约定：position_size_usd 字段名；缺失按 0 处理走基础模型。
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests

# ===== 常量 =====
POSITION_THRESHOLD_USD = 10_000_000  # ≥ 此值走强模型
CACHE_TTL_SECONDS = 3600            # 1 小时
USAGE_LOG_FILE = "model_usage.jsonl"

# ===== 模块级状态 =====
_cache: Dict[str, Dict[str, Any]] = {}  # ticker -> {"ts": float, "result": str, "model": str}


def _env(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val else default


def _base_model() -> str:
    return _env("KIMI_BASE_MODEL", "kimi-k2.5")


def _strong_model() -> str:
    return _env("KIMI_STRONG_MODEL", "kimi-k2-thinking")


def _api_url() -> str:
    return _env("KIMI_API_URL", "https://api.moonshot.cn/v1/chat/completions")


def _log_path() -> str:
    return os.path.join(_env("LOG_DIR", "logs"), USAGE_LOG_FILE)


def select_model(holding: Dict[str, Any]) -> Tuple[str, str]:
    """根据持仓金额选模型，返回 (model_name, reason)。"""
    pos = float(holding.get("position_size_usd") or 0)
    if pos >= POSITION_THRESHOLD_USD:
        return _strong_model(), f"strong: position=${pos/1_000_000:.0f}M"
    return _base_model(), f"base: position=${pos/1_000_000:.2f}M"


def _cache_get(ticker: str) -> Optional[str]:
    entry = _cache.get(ticker)
    if not entry:
        return None
    if time.time() - entry["ts"] > CACHE_TTL_SECONDS:
        _cache.pop(ticker, None)
        return None
    return entry["result"]


def _cache_put(ticker: str, model: str, result: str) -> None:
    _cache[ticker] = {"ts": time.time(), "result": result, "model": model}


def clear_cache() -> None:
    """清空缓存（测试/手动维护用）。"""
    _cache.clear()


def _record_usage(
    ticker: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    fallback_used: bool,
) -> None:
    """追加一行 JSONL 到 logs/model_usage.jsonl。"""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "holding_ticker": ticker,
        "model_name": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_ms": latency_ms,
        "fallback_used": fallback_used,
    }
    path = _log_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _call_kimi(
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 120,
) -> Tuple[str, int, int, int]:
    """底层 Kimi 调用。返回 (text, tokens_in, tokens_out, latency_ms)。

    抛出 requests.HTTPError（status_code 已设）让上层做 fallback 判定。
    """
    api_key = os.getenv("KIMI_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2500,
    }
    start = time.time()
    resp = requests.post(_api_url(), headers=headers, json=payload, timeout=timeout)
    latency_ms = int((time.time() - start) * 1000)
    # 触发 raise_for_status（503/429 等走 fallback）
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), latency_ms


def call_kimi_with_router(
    holding: Dict[str, Any],
    signal_strength: Any,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """主入口：缓存 → 选模型 → 调用 → fallback → 埋点。"""
    ticker = str(holding.get("ticker", "UNKNOWN"))
    signal_strength = signal_strength  # 预留参数，签名对齐 PRD

    # 1) 缓存命中
    cached = _cache_get(ticker)
    if cached is not None:
        # 缓存命中也补一条埋点，便于审计
        _record_usage(
            ticker=ticker,
            model=_cache[ticker].get("model", "cached"),
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            fallback_used=False,
        )
        return cached

    # 2) 选主模型
    primary, reason = select_model(holding)
    fallback_model = _base_model() if primary != _base_model() else None

    # 3) 调主模型
    try:
        text, tin, tout, lat = _call_kimi(primary, system_prompt, user_prompt)
        _cache_put(ticker, primary, text)
        _record_usage(ticker, primary, tin, tout, lat, fallback_used=False)
        return text
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        if status not in (503, 429) or fallback_model is None:
            # 不可降级：透传错误（埋点记录失败）
            _record_usage(
                ticker=ticker,
                model=primary,
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
                fallback_used=False,
            )
            raise
        # 4) Fallback 到基础模型
        text, tin, tout, lat = _call_kimi(fallback_model, system_prompt, user_prompt)
        _cache_put(ticker, fallback_model, text)
        _record_usage(ticker, fallback_model, tin, tout, lat, fallback_used=True)
        return text
