"""pytest 全局 fixture：隔离 data dir / vault，禁网模式跑纯逻辑测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """注入隔离环境并重置 settings 单例。"""
    env = {
        "INVESTLAB_DATA_DIR": str(tmp_path / "data"),
        "INVESTLAB_OBSIDIAN_VAULT": str(tmp_path / "vault"),
        "INVESTLAB_LLM_API_KEY": "",
        "TUSHARE_TOKEN": "",
        "TAVILY_API_KEY": "",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import investlab.config as cfg

    cfg._settings = None
    s = cfg.get_settings(refresh=True)
    yield s
    cfg._settings = None
