"""推送通知模块（未配置通道的离线路径）。"""

from __future__ import annotations

from investlab import notify


def test_status_unconfigured(isolated_env):
    st = notify.status()
    assert st["configured"] == []
    assert st["ntfy_topic"] is False and st["bark"] is False


def test_send_push_no_channels(isolated_env):
    assert notify.send_push("t", "b") == []


def test_channels_built_from_env(isolated_env):
    from investlab import config as cfg

    cfg.reset_settings_for_testing({
        "INVESTLAB_DATA_DIR": str(isolated_env.data_dir),
        "INVESTLAB_OBSIDIAN_VAULT": str(isolated_env.vault_path),
        "INVESTLAB_NOTIFY_NTFY_TOPIC": "my-topic",
    })
    try:
        notify_cfg = notify._channels()
        assert notify_cfg and notify_cfg[0][0] == "ntfy"
        assert notify_cfg[0][1].endswith("/my-topic")
        assert "ntfy.sh" in notify_cfg[0][1]
    finally:
        cfg._settings = None
