"""投研档案：加载、合并优先级、用户覆盖、观察清单种子。"""

from __future__ import annotations

import json

import pytest

from investlab import profiles
from investlab.profiles import (
    builtin_profiles,
    load_profile,
    profile_name,
    profile_summary,
    seed_watchlist_from_profile,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    profiles._load_cached.cache_clear()
    yield
    profiles._load_cached.cache_clear()


def test_builtin_default_ai_production():
    p = load_profile()
    assert p["name"] == "ai-production"
    assert set(p["markets"]) == {"CN", "HK", "US"}
    assert len(p["sector_focus"]) >= 5
    assert len(p["watchlist_seed"]) >= 10
    assert any("光模块" in s for s in p["sector_focus"])
    assert p["briefing"]["push_on_complete"] is True


def test_builtin_list_contains_template():
    names = builtin_profiles()
    assert "ai-production" in names and "template" in names


def test_user_profile_overrides_builtin(isolated_env):
    # data/profile.json 覆盖内置档案的字段
    user_file = isolated_env.data_dir / "profile.json"
    user_file.write_text(json.dumps({
        "name": "my-thesis",
        "sector_focus": ["创新药"],
        "watchlist_seed": ["600519.SS"],
    }), encoding="utf-8")
    p = load_profile(refresh=True)
    assert p["name"] == "my-thesis"
    assert p["sector_focus"] == ["创新药"]
    assert p["watchlist_seed"] == ["600519.SS"]
    # 未覆盖字段仍来自内置
    assert set(p["markets"]) == {"CN", "HK", "US"}


def test_env_selects_builtin_by_name(isolated_env, monkeypatch):
    monkeypatch.setenv("INVESTLAB_PROFILE", "template")
    p = load_profile(refresh=True)
    assert p["name"] == "template"
    assert p["sector_focus"] == []


def test_env_selects_external_json(isolated_env, tmp_path, monkeypatch):
    ext = tmp_path / "mine.json"
    ext.write_text(json.dumps({"name": "external", "thesis": "价值投资"}), encoding="utf-8")
    monkeypatch.setenv("INVESTLAB_PROFILE", str(ext))
    p = load_profile(refresh=True)
    assert p["name"] == "external" and p["thesis"] == "价值投资"


def test_sanitizes_unknown_keys(isolated_env, tmp_path, monkeypatch):
    ext = tmp_path / "hacked.json"
    ext.write_text(json.dumps({
        "name": "x",
        "evil_key": "should-not-pass",
        "data_dir": "/tmp/evil",
    }), encoding="utf-8")
    monkeypatch.setenv("INVESTLAB_PROFILE", str(ext))
    p = load_profile(refresh=True)
    assert "evil_key" not in p and "data_dir" not in p


def test_missing_profile_name_falls_back(isolated_env, monkeypatch):
    monkeypatch.setenv("INVESTLAB_PROFILE", "no-such-profile")
    p = load_profile(refresh=True)
    assert p["name"] == "ai-production"


def test_seed_watchlist_shape(isolated_env, monkeypatch):
    monkeypatch.setenv("INVESTLAB_PROFILE", "ai-production")
    profiles._load_cached.cache_clear()
    wl = seed_watchlist_from_profile()
    assert set(wl) == {"tickers", "companyKeywords", "macroKeywords"}
    assert isinstance(wl["companyKeywords"], dict)
    assert profiles._load_cached.cache_clear() is None


def test_news_uses_profile_keywords(isolated_env, monkeypatch):
    """watchlist.json 缺失时，新闻匹配层应拿到档案的种子。"""
    monkeypatch.setenv("INVESTLAB_PROFILE", "ai-production")
    profiles._load_cached.cache_clear()
    from investlab.datasources.news import load_watchlist

    wl = load_watchlist()
    assert "300308.SZ" in wl["tickers"]
    assert any("光模块" in k for k in wl["macroKeywords"])


def test_summary_shape():
    s = profile_summary()
    assert s["name"] == profile_name()
    assert isinstance(s["news_sources"], list)
