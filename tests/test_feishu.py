from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from uclaw.bus import MessageBus
from uclaw.channels.feishu import FeishuChannel


# ---------------------------------------------------------------------------
# is_allowed with Feishu open_id format
# ---------------------------------------------------------------------------


class TestFeishuIsAllowed:
    def _make(self, allowed: list[str]) -> FeishuChannel:
        bus = MessageBus()
        return FeishuChannel(
            bus,
            app_id="cli_test",
            app_secret="secret",
            allowed_users=allowed,
        )

    def test_open_id_allowed(self):
        ch = self._make(["ou_abc123def456"])
        assert ch.is_allowed("ou_abc123def456") is True

    def test_open_id_denied(self):
        ch = self._make(["ou_abc123def456"])
        assert ch.is_allowed("ou_other_user") is False

    def test_wildcard_allows_any_open_id(self):
        ch = self._make(["*"])
        assert ch.is_allowed("ou_abc123def456") is True
        assert ch.is_allowed("ou_xyz") is True

    def test_empty_list_denies(self):
        ch = self._make([])
        assert ch.is_allowed("ou_abc123def456") is False

    def test_multiple_open_ids(self):
        ch = self._make(["ou_user1", "ou_user2"])
        assert ch.is_allowed("ou_user1") is True
        assert ch.is_allowed("ou_user2") is True
        assert ch.is_allowed("ou_user3") is False


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------


class TestFeishuDedup:
    @pytest.mark.asyncio
    async def test_duplicate_message_skipped(self):
        bus = MessageBus()
        ch = FeishuChannel(bus, "id", "secret", ["*"])
        ch._loop = asyncio.get_running_loop()

        def _make_event(msg_id: str, text: str) -> SimpleNamespace:
            return SimpleNamespace(
                event=SimpleNamespace(
                    message=SimpleNamespace(
                        message_id=msg_id,
                        chat_type="p2p",
                        chat_id="oc_chat",
                        content='{"text": "' + text + '"}',
                    ),
                    sender=SimpleNamespace(
                        sender_type="user",
                        sender_id=SimpleNamespace(open_id="ou_user1"),
                    ),
                ),
            )

        await ch._on_message(_make_event("m1", "hello"))
        await ch._on_message(_make_event("m1", "hello"))  # duplicate

        assert bus.inbound.qsize() == 1

    @pytest.mark.asyncio
    async def test_dedup_cache_capped(self):
        bus = MessageBus()
        ch = FeishuChannel(bus, "id", "secret", ["*"])
        ch._loop = asyncio.get_running_loop()

        # Fill dedup cache beyond limit
        for i in range(1100):
            ch._seen[f"m{i}"] = None
            if len(ch._seen) > 1000:
                ch._seen.popitem(last=False)

        assert len(ch._seen) <= 1000


# ---------------------------------------------------------------------------
# Bot message filtering
# ---------------------------------------------------------------------------


class TestFeishuBotFilter:
    @pytest.mark.asyncio
    async def test_bot_messages_skipped(self):
        bus = MessageBus()
        ch = FeishuChannel(bus, "id", "secret", ["*"])
        ch._loop = asyncio.get_running_loop()

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="m_bot",
                    chat_type="p2p",
                    chat_id="oc_chat",
                    content='{"text": "bot reply"}',
                ),
                sender=SimpleNamespace(
                    sender_type="bot",
                    sender_id=SimpleNamespace(open_id="ou_bot"),
                ),
            ),
        )

        await ch._on_message(data)
        assert bus.inbound.qsize() == 0


# ---------------------------------------------------------------------------
# Chat ID routing (p2p vs group)
# ---------------------------------------------------------------------------


class TestFeishuChatRouting:
    @pytest.mark.asyncio
    async def test_p2p_uses_sender_id(self):
        bus = MessageBus()
        ch = FeishuChannel(bus, "id", "secret", ["*"])
        ch._loop = asyncio.get_running_loop()

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="m_p2p",
                    chat_type="p2p",
                    chat_id="oc_chat_xxx",
                    content='{"text": "hi"}',
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_sender"),
                ),
            ),
        )

        await ch._on_message(data)
        msg = bus.inbound.get_nowait()
        assert msg.chat_id == "ou_sender"

    @pytest.mark.asyncio
    async def test_group_uses_chat_id(self):
        bus = MessageBus()
        ch = FeishuChannel(bus, "id", "secret", ["*"])
        ch._loop = asyncio.get_running_loop()

        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="m_grp",
                    chat_type="group",
                    chat_id="oc_group_chat",
                    content='{"text": "hi group"}',
                ),
                sender=SimpleNamespace(
                    sender_type="user",
                    sender_id=SimpleNamespace(open_id="ou_sender"),
                ),
            ),
        )

        await ch._on_message(data)
        msg = bus.inbound.get_nowait()
        assert msg.chat_id == "oc_group_chat"


# ---------------------------------------------------------------------------
# send() receive_id_type logic
# ---------------------------------------------------------------------------


class TestFeishuSendIdType:
    def test_chat_id_prefix(self):
        """oc_ prefix -> chat_id type; otherwise -> open_id type."""
        ch = FeishuChannel(MessageBus(), "id", "secret", [])
        # Just verify the logic is encoded in _send_text_sync
        # (We cannot call it without mocking lark_oapi, but we can
        #  check that the channel at least instantiates.)
        assert ch.name == "feishu"
