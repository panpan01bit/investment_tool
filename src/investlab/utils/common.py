"""通用工具：原子写、文件名净化、日期处理、日志、TTL 缓存。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import get_settings

CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def today_str(fmt: str = "%Y-%m-%d") -> str:
    return now_cn().strftime(fmt)


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y%m%d"):
        try:
            return datetime.strptime(str(s).strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


_SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def safe_filename(name: str, max_len: int = 80) -> str:
    """Obsidian/跨平台安全文件名。"""
    cleaned = _SAFE_NAME_RE.sub(" ", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return (cleaned or "untitled")[:max_len]


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def setup_logging(name: str = "investlab") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        level = getattr(logging, get_settings().log_level.upper(), logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


# ------------------------------------------------------------------ TTL 缓存
# 纯 JSON 文件实现（data/cache_store.json）：本地单用户场景足够，
# 结构 {"<kind>:<sha256(key)>": {"expires": float, "meta": kind, "value": ...}}


_CACHE_LOCK = threading.Lock()


def _cache_key(kind: str, params) -> tuple[str, str]:
    if not re.fullmatch(r"\w+", kind or ""):
        raise ValueError("cache kind 只能包含字母数字下划线")
    blob = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return kind, digest


def _cache_file() -> Path:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "cache_store.json"


def _load_store() -> dict:
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_store(store: dict) -> None:
    try:
        atomic_write_text(_cache_file(), json.dumps(store, ensure_ascii=False))
    except Exception:
        pass


def cache_get(kind: str, params, max_age_s: float):
    """命中且未过期返回值，否则 None。"""
    kind_, digest = _cache_key(kind, params)
    with _CACHE_LOCK:
        entry = _load_store().get(f"{kind_}:{digest}")
    if not entry:
        return None
    if time.time() - float(entry.get("saved_at", 0)) > max_age_s:
        return None
    return entry.get("value")


def cache_put(kind: str, params, value, ttl_s: float = 300.0) -> None:
    kind_, digest = _cache_key(kind, params)
    key = f"{kind_}:{digest}"
    with _CACHE_LOCK:
        store = _load_store()
        store[key] = {
            "meta": kind_,
            "saved_at": time.time(),
            "ttl_s": ttl_s,
            "value": value,
        }
        # 概率性顺带清理过期项，避免文件膨胀
        if (time.time() % 20) < 1:
            now = time.time()
            store = {
                k: v
                for k, v in store.items()
                if now - float(v.get("saved_at", 0)) <= float(v.get("ttl_s", 0)) * 4
            }
        _save_store(store)


def invalidate_cache(kind_filter: str = "") -> int:
    """清空缓存；传 kind 时只清该类。返回清理条数。"""
    kind_ = _cache_key(kind_filter or "_all_", [])[0] if kind_filter else ""
    with _CACHE_LOCK:
        store = _load_store()
        before = len(store)
        if kind_filter:
            store = {k: v for k, v in store.items() if v.get("meta") != kind_}
        else:
            store = {}
        removed = before - len(store)
        _save_store(store)
        return removed
