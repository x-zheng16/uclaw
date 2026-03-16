import json

import pytest

from uclaw.config import BridgeConfig, load_config


def test_load_config(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "telegram": {"enabled": True, "token": "fake:token", "allowed_users": ["123"]},
                "feishu": {"enabled": False, "app_id": "", "app_secret": "", "allowed_users": []},
                "claude": {"workspace": "/tmp/workspace", "permission_mode": "bypassPermissions"},
            }
        )
    )
    cfg = load_config(config_file)
    assert cfg.telegram.enabled is True
    assert cfg.telegram.token == "fake:token"
    assert cfg.feishu.enabled is False
    assert cfg.claude.workspace == "/tmp/workspace"


def test_load_config_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.json")
