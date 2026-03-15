from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from .bus import MessageBus, OutboundMessage
from .channels.manager import ChannelManager
from .config import load_config
from .cron.service import CronScheduler
from .cron.types import CronStore
from .router import SessionRouter

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".claude-bridge"
CONFIG_PATH = DATA_DIR / "config.json"
CRON_PATH = DATA_DIR / "cron" / "jobs.json"


async def execute_cron_job(message: str, channel: str, chat_id: str) -> str:
    """Execute a cron job using one-shot query()."""
    from claude_agent_sdk import ClaudeAgentOptions, query
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    config = load_config(CONFIG_PATH)
    options = ClaudeAgentOptions(
        cwd=config.claude.workspace,
        permission_mode=config.claude.permission_mode,
        setting_sources=config.claude.setting_sources,
    )
    result_text = ""
    async for msg in query(prompt=message, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    result_text += block.text
    return result_text


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists():
        logger.error("Config not found at %s. Copy config.example.json and edit it.", CONFIG_PATH)
        return

    config = load_config(CONFIG_PATH)
    bus = MessageBus()

    # Build channel adapters
    channels: dict = {}
    if config.telegram.enabled:
        from .channels.telegram import TelegramChannel

        channels["telegram"] = TelegramChannel(
            bus=bus,
            token=config.telegram.token,
            allowed_users=config.telegram.allowed_users,
        )
    if config.feishu.enabled:
        from .channels.feishu import FeishuChannel

        channels["feishu"] = FeishuChannel(
            bus=bus,
            app_id=config.feishu.app_id,
            app_secret=config.feishu.app_secret,
            allowed_users=config.feishu.allowed_users,
        )

    if not channels:
        logger.error("No channels enabled. Enable telegram or feishu in config.json.")
        return

    channel_mgr = ChannelManager(bus=bus, channels=channels)
    router = SessionRouter(config=config, bus=bus, data_dir=DATA_DIR)

    # Cron
    CRON_PATH.parent.mkdir(parents=True, exist_ok=True)
    cron_store = CronStore(path=CRON_PATH)
    if CRON_PATH.exists():
        cron_store.load()
    cron = CronScheduler(store=cron_store, bus=bus, execute_fn=execute_cron_job)

    # Copy heartbeat template if workspace doesn't have one
    workspace = Path(config.claude.workspace).expanduser()
    heartbeat_file = workspace / "HEARTBEAT.md"
    if not heartbeat_file.exists():
        template = Path(__file__).parent.parent / "templates" / "HEARTBEAT.md"
        if template.exists():
            workspace.mkdir(parents=True, exist_ok=True)
            heartbeat_file.write_text(template.read_text())
            logger.info("Copied HEARTBEAT.md template to %s", heartbeat_file)

    # Shutdown handler
    async def shutdown() -> None:
        logger.info("Shutting down...")
        cron.stop()
        await channel_mgr.stop_all()
        await router.shutdown()
        logger.info("Shutdown complete")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    logger.info("claude-bridge started (channels: %s)", list(channels.keys()))

    await asyncio.gather(
        channel_mgr.start_all(),
        router.run(),
        cron.start(),
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
