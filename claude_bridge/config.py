from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    allowed_users: list[str] = field(default_factory=list)


@dataclass
class FeishuConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allowed_users: list[str] = field(default_factory=list)


@dataclass
class ClaudeConfig:
    workspace: str = "~/workspace"
    permission_mode: str = "bypassPermissions"
    setting_sources: list[str] = field(default_factory=lambda: ["user", "project"])


@dataclass
class BridgeConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)


def load_config(path: Path) -> BridgeConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    data = json.loads(path.read_text())
    return BridgeConfig(
        telegram=TelegramConfig(**data.get("telegram", {})),
        feishu=FeishuConfig(**data.get("feishu", {})),
        claude=ClaudeConfig(**data.get("claude", {})),
    )
