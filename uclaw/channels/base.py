from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from uclaw.bus import InboundMessage, MessageBus

logger = logging.getLogger(__name__)


class BaseChannel(ABC):
    name: str = "base"

    def __init__(self, bus: MessageBus, allowed_users: list[str]) -> None:
        self.bus = bus
        self.allowed_users = allowed_users
        self._running = True

    def is_allowed(self, sender_id: str) -> bool:
        if "*" in self.allowed_users:
            return True
        return sender_id in self.allowed_users

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        text: str,
        media: list[str] | None = None,
    ) -> None:
        if not self.is_allowed(sender_id):
            logger.warning("%s: rejected message from %s", self.name, sender_id)
            return
        msg = InboundMessage(
            channel=self.name,
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            media=media or [],
        )
        await self.bus.publish_inbound(msg)

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(
        self, chat_id: str, text: str, media: list[str] | None = None
    ) -> None: ...
