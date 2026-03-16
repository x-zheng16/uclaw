from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class InboundMessage:
    channel: str
    chat_id: str
    sender_id: str
    text: str
    media: list[str] = field(default_factory=list)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    text: str
    kind: Literal["new", "edit"] = "new"
    message_id: int | None = None
    media: list[str] | None = None


class MessageBus:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()
