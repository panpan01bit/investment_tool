"""投研档案（profile）：把"投研需求与使用特点"做成可切换、可分享的配置层。

三层合并（后者覆盖前者）：
  1. 内置档案   src/investlab/profiles/<name>.json   随仓库分发（ai-production / template）
  2. 用户档案   <data_dir>/profile.json              用户个人定制（gitignore，不入库）
  3. 环境选择   INVESTLAB_PROFILE=名字或JSON绝对路径

约定：档案只放"研究框架与使用习惯"（市场/主线/关注链/关键词/新闻源/晨报习惯），
API 密钥一律放 .env（见 .env.example），两者分离。

使用：
  from .profiles import load_profile, profile_summary
  prof = load_profile()
  prof["macro_keywords"]  # 已合并用户覆盖
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..config import get_settings
from ..utils.common import setup_logging

log = setup_logging("investlab.profiles")

BUILTIN_DIR = Path(__file__).parent
DEFAULT_PROFILE = "ai-production"

_ALLOWED_TOP_KEYS = {
    "name", "description", "markets", "investor_type", "thesis",
    "sector_focus", "watchlist_seed", "company_keywords", "macro_keywords",
    "news_sources", "briefing", "default_symbols", "llm",
}


def _builtin_path(name: str) -> Path | None:
    """内置档案名 → 路径；含路径穿越防护（仅允许字母数字连字符下划线）。"""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name):
        return None
    p = BUILTIN_DIR / f"{name}.json"
    return p if p.is_file() else None


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        log.warning("档案 JSON 解析失败 %s: %s", p, exc)
        return {}


def _sanitize(profile: dict) -> dict:
    """只保留白名单字段，防未知键进入运行时。"""
    return {k: v for k, v in profile.items() if k in _ALLOWED_TOP_KEYS}


@lru_cache(maxsize=4)
def _load_cached(cache_key: tuple) -> dict:
    source_name, user_file, override_file = cache_key
    merged: dict = {}

    # 1) 内置档案（默认 ai-production；INVESTLAB_PROFILE 可指向内置名或绝对路径）
    builtin_name = (source_name or "").strip() or DEFAULT_PROFILE
    p = Path(builtin_name)
    if p.is_absolute() and p.is_file():
        merged.update(_sanitize(_read_json(p)))
    else:
        bp = _builtin_path(builtin_name)
        if bp:
            merged.update(_sanitize(_read_json(bp)))
        else:
            log.warning("档案 %r 不存在（内置或路径），使用默认 %s",
                        builtin_name, DEFAULT_PROFILE)
            bp = _builtin_path(DEFAULT_PROFILE)
            if bp:
                merged.update(_sanitize(_read_json(bp)))

    # 2) 用户个人档案 data/profile.json（若有则逐字段覆盖）
    if user_file and Path(user_file).is_file():
        merged.update(_sanitize(_read_json(Path(user_file))))

    # 3) 显式 override（测试注入）
    if override_file and Path(override_file).is_file():
        merged.update(_sanitize(_read_json(Path(override_file))))

    # 兜底
    merged.setdefault("name", DEFAULT_PROFILE)
    merged.setdefault("markets", ["CN", "HK", "US"])
    merged.setdefault("macro_keywords", [])
    merged.setdefault("company_keywords", {})
    merged.setdefault("news_sources", [])
    merged.setdefault("sector_focus", [])
    merged.setdefault("watchlist_seed", [])
    merged.setdefault("briefing", {})
    merged.setdefault("default_symbols", [])
    return merged


def load_profile(refresh: bool = False) -> dict:
    """加载合并后的投研档案。INVESTLAB_PROFILE 实时读取（便于临时切换/测试）。"""
    import os

    settings = get_settings()
    user_file = settings.data_dir / "profile.json"
    source = (os.environ.get("INVESTLAB_PROFILE")
              or getattr(settings, "profile", "") or "").strip()
    key = (source, str(user_file), "")  # override 预留
    if refresh:
        _load_cached.cache_clear()
    return dict(_load_cached(key))


def profile_name() -> str:
    return str(load_profile().get("name") or DEFAULT_PROFILE)


def profile_summary() -> dict:
    """doctor / 设置页展示用。"""
    p = load_profile()
    return {
        "name": p.get("name"),
        "description": p.get("description"),
        "markets": p.get("markets"),
        "thesis": p.get("thesis"),
        "sector_focus_count": len(p.get("sector_focus") or []),
        "watchlist_seed_count": len(p.get("watchlist_seed") or []),
        "macro_keywords_count": len(p.get("macro_keywords") or []),
        "news_sources": [s.get("name") for s in (p.get("news_sources") or [])],
        "briefing": p.get("briefing") or {},
    }


def builtin_profiles() -> list[str]:
    """内置档案名列表（供 CLI/设置页枚举）。"""
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.json"))


def seed_watchlist_from_profile() -> dict:
    """用档案生成初始观察清单（data/watchlist.json 缺失时由 investlab init 调用）。"""
    p = load_profile()
    return {
        "tickers": list(p.get("watchlist_seed") or []),
        "companyKeywords": dict(p.get("company_keywords") or {}),
        "macroKeywords": list(p.get("macro_keywords") or []),
    }
