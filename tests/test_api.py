"""API 冒烟（FastAPI TestClient，离线路径参数校验与降级行为）。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client(isolated_env):
    from fastapi.testclient import TestClient

    from investlab.api.main import app

    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_settings_status_no_secret_leak(client):
    data = client.get("/api/settings/status").json()
    assert "tokens" in data
    blob = str(data)
    # 状态接口只允许“已配置/未配置”文案，不回显任何密钥
    assert "sk-" not in blob and "Bearer" not in blob


def test_briefing_date_validation(client):
    # 格式非法 → 400；格式合法但无数据 → 404；
    # 路径穿越字符串会被路由层直接丢弃（404）或校验拦截（400），都不允许进入文件层
    assert client.get("/api/briefings/not-a-date").status_code == 400
    assert client.get("/api/briefings/%2e%2e%2fetc").status_code in (400, 404)
    assert client.get("/api/briefings/2030-01-01").status_code == 404


def test_report_id_validation(client):
    bad = client.get("/api/reports/../../etc/passwd")
    assert bad.status_code in (400, 404, 422)
    assert client.get("/api/reports/nothex").status_code == 400
    missing = client.get("/api/reports/" + "0" * 16)
    assert missing.status_code == 404


def test_search_empty_query(client):
    resp = client.post("/api/search", json={"query": "", "max_results": 5})
    assert resp.status_code == 422  # pydantic min_length


def test_tracks_endpoint_shape(client):
    data = client.get("/api/tracks").json()
    assert "secondary_tracks" in data and "top_ranking" in data
