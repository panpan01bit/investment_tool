"""社媒热度聚合：mock 各源响应，验证打分/降级/状态语义（离线）。"""

from __future__ import annotations

import pytest

from investlab.datasources import social


def _patch_source(monkeypatch, name, fn):
    monkeypatch.setattr(social, f"_{name}", fn)


def test_heat_none_when_all_fail(monkeypatch, isolated_env):
    for name in ("reddit", "hn", "polymarket", "github", "stocktwits"):
        _patch_source(monkeypatch, name,
                      lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down")))
    monkeypatch.setattr(social, "_stocktwits", lambda s=None: ([], "skipped(非美股代码)"))
    res = social.social_pulse("liquid cooling", use_cache=False)
    assert res["heat"] is None
    assert res["heat_label"] == "无数据"
    assert all(v.startswith("error") or v.startswith("skipped")
               for v in res["source_status"].values())


def test_reddit_items_normalized(monkeypatch, isolated_env):
    fake = {
        "data": {"children": [
            {"data": {"title": "NVDA earnings thread", "score": 1500,
                      "num_comments": 300, "permalink": "/r/stocks/x",
                      "created_utc": 1750000000, "subreddit": "stocks"}},
        ]},
    }
    monkeypatch.setattr(social, "http_get_json", lambda *a, **k: fake)
    items, status = social._reddit("nvda", social.datetime.now(social.timezone.utc))
    assert status == "ok"
    assert items[0]["engagement"] == 1500
    assert items[0]["extra"]["subreddit"] == "stocks"
    assert items[0]["url"].startswith("https://www.reddit.com/r/stocks")


def test_stocktwits_skipped_for_cn(monkeypatch, isolated_env):
    items, status = social._stocktwits("300308")
    assert status.startswith("skipped")
    items2, status2 = social._stocktwits(None)
    assert status2.startswith("skipped")


def test_stocktwits_bullish_summary(monkeypatch, isolated_env):
    fake = {"messages": [
        {"id": 1, "body": "to the moon", "created_at": "2026-08-01",
         "user": {"username": "a"}, "sentiment": {"basic": "Bullish"}},
        {"id": 2, "body": "overvalued", "created_at": "2026-08-01",
         "user": {"username": "b"}, "sentiment": {"basic": "Bearish"}},
        {"id": 3, "body": "watching", "created_at": "2026-08-01",
         "user": {"username": "c"}, "sentiment": None},
    ]}
    monkeypatch.setattr(social, "http_get_json", lambda *a, **k: fake)
    items, status = social._stocktwits("NVDA")
    assert status == "ok"
    summary = [x for x in items if x["metric"] == "bullish_pct"]
    assert summary and summary[0]["extra"]["bullish_pct"] == 50.0


def test_polymarket_probability_parsing(monkeypatch, isolated_env):
    fake = {"events": [{
        "title": "Fed cut in Sept?", "slug": "fed-sept",
        "volume24hr": 66234.0,
        "markets": [{"outcomePrices": "[\"0.86\", \"0.14\"]"}],
    }]}
    monkeypatch.setattr(social, "http_get_json", lambda *a, **k: fake)
    items, status = social._polymarket("fed", social.datetime.now(social.timezone.utc))
    assert status == "ok"
    assert items[0]["extra"]["implied_probability"] == 0.86
    assert items[0]["engagement"] == 66234


def test_pulse_scoring_and_cache(monkeypatch, isolated_env):
    monkeypatch.setattr(
        social, "_reddit",
        lambda q, since: ([{"source": "reddit", "title": "hot", "url": "u",
                            "metric": "upvotes", "engagement": 2000,
                            "created": "2026-08-01", "extra": {}}], "ok"))
    monkeypatch.setattr(social, "_hn", lambda q, since: ([], "no-results"))
    monkeypatch.setattr(social, "_polymarket", lambda q, since: ([], "no-results"))
    monkeypatch.setattr(social, "_github", lambda q, since: ([], "no-results"))
    monkeypatch.setattr(social, "_stocktwits", lambda s=None: ([], "skipped(非美股代码)"))

    res = social.social_pulse("nvda", symbol="NVDA", use_cache=False)
    assert res["heat"] is not None and res["heat"] > 0
    assert res["heat_label"] in ("平淡", "升温", "火热", "白热化")
    assert res["source_status"]["hn"] == "no-results"
    assert any(i["source"] == "reddit" for i in res["items"])

    # 二次调用命中缓存（源函数被替换成会抛错的版本也不影响）
    monkeypatch.setattr(social, "_reddit",
                        lambda q, since: (_ for _ in ()).throw(RuntimeError()))
    res2 = social.social_pulse("nvda", symbol="NVDA", use_cache=True)
    assert res2["heat"] is not None


def test_pulse_empty_query():
    res = social.social_pulse("  ")
    assert res["heat"] is None and res["items"] == []


@pytest.mark.parametrize("heat,expected", [
    (None, "无数据"), (5, "冷清"), (30, "平淡"), (50, "升温"),
    (70, "火热"), (95, "白热化"),
])
def test_heat_labels(heat, expected):
    assert social._heat_label(heat) == expected
