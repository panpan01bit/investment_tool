"""config：环境变量读取、旧变量名别名、优先级。"""

from __future__ import annotations

import investlab.config as cfg


def test_env_precedence_and_alias(isolated_env, monkeypatch):
    # 新名字优先于旧别名
    monkeypatch.setenv("INVESTLAB_LLM_API_KEY", "new-key")
    monkeypatch.setenv("KIMI_API_KEY", "old-key")
    cfg._settings = None
    s = cfg.build_settings()
    assert s.llm_api_key == "new-key"

    monkeypatch.delenv("INVESTLAB_LLM_API_KEY")
    s = cfg.build_settings()
    assert s.llm_api_key == "old-key"


def test_vault_defaults_under_data(isolated_env):
    assert isolated_env.vault_path.is_absolute()
    isolated_env.ensure_dirs()
    assert (isolated_env.data_dir / "briefings").exists()


def test_token_status_masks_absence(isolated_env):
    st = isolated_env.token_status()
    assert st["llm"] is False
    assert st["vision"] is False
