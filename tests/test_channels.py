from __future__ import annotations

import asyncio

import pytest

from uclaw.bus import MessageBus, OutboundMessage
from uclaw.channels.base import BaseChannel
from uclaw.channels.manager import ChannelManager
from uclaw.channels.telegram import split_message


# ---------------------------------------------------------------------------
# Fake channel for testing base + manager
# ---------------------------------------------------------------------------


class FakeChannel(BaseChannel):
    name = "fake"

    def __init__(self, bus: MessageBus, allowed_users: list[str]) -> None:
        super().__init__(bus, allowed_users)
        self.started = False
        self.stopped = False
        self.sent: list[tuple[str, str, list[str] | None]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send(
        self, chat_id: str, text: str, media: list[str] | None = None
    ) -> None:
        self.sent.append((chat_id, text, media))


# ---------------------------------------------------------------------------
# BaseChannel.is_allowed
# ---------------------------------------------------------------------------


class TestIsAllowed:
    def test_allowed_user(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=["alice", "bob"])
        assert ch.is_allowed("alice") is True
        assert ch.is_allowed("bob") is True

    def test_denied_user(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=["alice"])
        assert ch.is_allowed("eve") is False

    def test_wildcard_allows_all(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=["*"])
        assert ch.is_allowed("anyone") is True
        assert ch.is_allowed("") is True

    def test_empty_list_denies_all(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=[])
        assert ch.is_allowed("alice") is False


# ---------------------------------------------------------------------------
# BaseChannel._handle_message
# ---------------------------------------------------------------------------


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_publishes_when_allowed(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=["alice"])
        await ch._handle_message("alice", "chat1", "hello", media=None)

        msg = bus.inbound.get_nowait()
        assert msg.channel == "fake"
        assert msg.chat_id == "chat1"
        assert msg.sender_id == "alice"
        assert msg.text == "hello"

    @pytest.mark.asyncio
    async def test_publishes_with_media(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=["*"])
        await ch._handle_message("bob", "chat2", "pic", media=["url1"])

        msg = bus.inbound.get_nowait()
        assert msg.media == ["url1"]

    @pytest.mark.asyncio
    async def test_rejects_when_not_allowed(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=["alice"])
        await ch._handle_message("eve", "chat1", "hello")

        assert bus.inbound.qsize() == 0


# ---------------------------------------------------------------------------
# ChannelManager outbound dispatch
# ---------------------------------------------------------------------------


class TestChannelManager:
    @pytest.mark.asyncio
    async def test_dispatch_outbound(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=[])
        manager = ChannelManager(bus, {"fake": ch})

        msg = OutboundMessage(channel="fake", chat_id="c1", text="reply")
        await bus.publish_outbound(msg)

        task = asyncio.create_task(manager._dispatch_outbound())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(ch.sent) == 1
        assert ch.sent[0] == ("c1", "reply", None)

    @pytest.mark.asyncio
    async def test_dispatch_ignores_unknown_channel(self):
        bus = MessageBus()
        ch = FakeChannel(bus, allowed_users=[])
        manager = ChannelManager(bus, {"fake": ch})

        msg = OutboundMessage(channel="unknown", chat_id="c1", text="reply")
        await bus.publish_outbound(msg)

        task = asyncio.create_task(manager._dispatch_outbound())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(ch.sent) == 0


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------


class TestSplitMessage:
    def test_short_text_unchanged(self):
        assert split_message("hello") == ["hello"]

    def test_empty_text(self):
        assert split_message("") == [""]

    def test_long_text_split(self):
        text = "a" * 10000
        parts = split_message(text, max_len=4096)
        assert all(len(p) <= 4096 for p in parts)
        assert "".join(parts) == text

    def test_split_at_newline_when_possible(self):
        # Build text with newlines so split can break at a newline
        line = "x" * 100 + "\n"
        text = line * 50  # 5050 chars
        parts = split_message(text, max_len=4096)
        assert len(parts) == 2
        # First part should end at a newline boundary
        assert parts[0].endswith("\n")
        assert "".join(parts) == text

    def test_custom_max_len(self):
        text = "abcdefghij"  # 10 chars
        parts = split_message(text, max_len=3)
        assert parts == ["abc", "def", "ghi", "j"]
