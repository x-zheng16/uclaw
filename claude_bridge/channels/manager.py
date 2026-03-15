from __future__ import annotations

import asyncio
import logging

from claude_bridge.bus import MessageBus
from claude_bridge.channels.base import BaseChannel

logger = logging.getLogger(__name__)

RESTART_BACKOFF = 5


class ChannelManager:
    def __init__(self, bus: MessageBus, channels: dict[str, BaseChannel]) -> None:
        self.bus = bus
        self.channels = channels
        self._tasks: list[asyncio.Task] = []

    async def start_all(self) -> None:
        self._tasks.append(asyncio.create_task(self._dispatch_outbound()))
        for name, ch in self.channels.items():
            self._tasks.append(asyncio.create_task(self._run_channel(name, ch)))

    async def stop_all(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for ch in self.channels.values():
            await ch.stop()

    async def _dispatch_outbound(self) -> None:
        while True:
            msg = await self.bus.consume_outbound()
            ch = self.channels.get(msg.channel)
            if ch is None:
                logger.warning("no channel registered for %r", msg.channel)
                continue
            try:
                await ch.send(msg.chat_id, msg.text, msg.media)
            except Exception:
                logger.exception("failed to send on %s", msg.channel)

    async def _run_channel(self, name: str, ch: BaseChannel) -> None:
        while True:
            try:
                logger.info("starting channel %s", name)
                await ch.start()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "channel %s crashed, restarting in %ds", name, RESTART_BACKOFF
                )
                await asyncio.sleep(RESTART_BACKOFF)
