"""Tests for typing indicator feature.

Spec:
- OutboundMessage supports kind="typing"
- BaseChannel.send_typing() is a no-op by default
- ChannelManager routes typing messages to send_typing(), not send()
- SessionRouter emits typing events every 4s during query processing
- Typing loop is cancelled once query + response collection completes
"""

from __future__ import annotations

import asyncio

import pytest

from uclaw.bus import MessageBus, OutboundMessage
from uclaw.channels.base import BaseChannel
from uclaw.channels.manager import ChannelManager


# ---------------------------------------------------------------------------
# Fake channel that tracks send_typing calls
# ---------------------------------------------------------------------------


class FakeTypingChannel(BaseChannel):
    name = "fake"

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(bus, allowed_users=["*"])
        self.sent: list[tuple[str, str, list[str] | None]] = []
        self.typing_calls: list[str] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(
        self, chat_id: str, text: str, media: list[str] | None = None
    ) -> None:
        self.sent.append((chat_id, text, media))

    async def send_typing(self, chat_id: str) -> None:
        self.typing_calls.append(chat_id)


# ---------------------------------------------------------------------------
# OutboundMessage kind="typing"
# ---------------------------------------------------------------------------


class TestOutboundTypingKind:
    def test_typing_kind_accepted(self):
        msg = OutboundMessage(channel="tg", chat_id="1", text="", kind="typing")
        assert msg.kind == "typing"

    def test_default_kind_unchanged(self):
        msg = OutboundMessage(channel="tg", chat_id="1", text="hi")
        assert msg.kind == "new"


# ---------------------------------------------------------------------------
# BaseChannel.send_typing default is no-op
# ---------------------------------------------------------------------------


class TestBaseChannelSendTyping:
    @pytest.mark.asyncio
    async def test_default_send_typing_is_noop(self):
        bus = MessageBus()
        ch = FakeTypingChannel(bus)
        # Override send_typing to use base class version
        await BaseChannel.send_typing(ch, "chat1")
        # Should not raise, should not record anything
        assert ch.typing_calls == []


# ---------------------------------------------------------------------------
# ChannelManager routes typing to send_typing
# ---------------------------------------------------------------------------


class TestChannelManagerTypingRouting:
    @pytest.mark.asyncio
    async def test_typing_message_calls_send_typing(self):
        bus = MessageBus()
        ch = FakeTypingChannel(bus)
        manager = ChannelManager(bus, {"fake": ch})

        msg = OutboundMessage(channel="fake", chat_id="c1", text="", kind="typing")
        await bus.publish_outbound(msg)

        task = asyncio.create_task(manager._dispatch_outbound())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert ch.typing_calls == ["c1"]
        assert ch.sent == []

    @pytest.mark.asyncio
    async def test_normal_message_calls_send_not_typing(self):
        bus = MessageBus()
        ch = FakeTypingChannel(bus)
        manager = ChannelManager(bus, {"fake": ch})

        msg = OutboundMessage(channel="fake", chat_id="c1", text="hello")
        await bus.publish_outbound(msg)

        task = asyncio.create_task(manager._dispatch_outbound())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert ch.sent == [("c1", "hello", None)]
        assert ch.typing_calls == []
