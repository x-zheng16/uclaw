from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uclaw.bus import MessageBus
from uclaw.channels.telegram import TelegramChannel


class TestOnVoice:
    """Test _on_voice handler in TelegramChannel."""

    @pytest.mark.asyncio
    async def test_voice_message_downloads_and_transcribes(self, tmp_path):
        bus = MessageBus()
        ch = TelegramChannel(bus, token="fake-token", allowed_users=["*"])

        # Build a fake Update with voice
        fake_file = AsyncMock()
        fake_file.download_to_drive = AsyncMock()

        fake_voice = MagicMock()
        fake_voice.file_unique_id = "abc123"
        fake_voice.get_file = AsyncMock(return_value=fake_file)

        fake_message = MagicMock()
        fake_message.voice = fake_voice
        fake_message.audio = None

        fake_user = MagicMock()
        fake_user.id = 42

        fake_chat = MagicMock()
        fake_chat.id = 100

        update = MagicMock()
        update.effective_message = fake_message
        update.effective_user = fake_user
        update.effective_chat = fake_chat

        with (
            patch(
                "uclaw.channels.telegram.transcribe", new_callable=AsyncMock
            ) as mock_transcribe,
            patch("uclaw.channels.telegram.MEDIA_DIR", tmp_path),
        ):
            mock_transcribe.return_value = "transcribed text"
            await ch._on_voice(update, None)

        # Should have called transcribe with the file path
        mock_transcribe.assert_called_once()
        call_path = mock_transcribe.call_args[0][0]
        assert call_path.endswith("abc123.ogg")

        # Should have downloaded the file
        fake_file.download_to_drive.assert_called_once()

        # Should have published to the bus
        msg = bus.inbound.get_nowait()
        assert msg.text == "transcribed text"
        assert msg.sender_id == "42"
        assert msg.chat_id == "100"

    @pytest.mark.asyncio
    async def test_audio_message_uses_audio_attr(self, tmp_path):
        bus = MessageBus()
        ch = TelegramChannel(bus, token="fake-token", allowed_users=["*"])

        fake_file = AsyncMock()
        fake_file.download_to_drive = AsyncMock()

        fake_audio = MagicMock()
        fake_audio.file_unique_id = "audio456"
        fake_audio.get_file = AsyncMock(return_value=fake_file)

        fake_message = MagicMock()
        fake_message.voice = None
        fake_message.audio = fake_audio

        fake_user = MagicMock()
        fake_user.id = 7

        fake_chat = MagicMock()
        fake_chat.id = 200

        update = MagicMock()
        update.effective_message = fake_message
        update.effective_user = fake_user
        update.effective_chat = fake_chat

        with (
            patch(
                "uclaw.channels.telegram.transcribe", new_callable=AsyncMock
            ) as mock_transcribe,
            patch("uclaw.channels.telegram.MEDIA_DIR", tmp_path),
        ):
            mock_transcribe.return_value = "audio text"
            await ch._on_voice(update, None)

        call_path = mock_transcribe.call_args[0][0]
        assert "audio456" in call_path

    @pytest.mark.asyncio
    async def test_voice_skips_if_no_message(self):
        bus = MessageBus()
        ch = TelegramChannel(bus, token="fake-token", allowed_users=["*"])

        update = MagicMock()
        update.effective_message = None
        update.effective_user = None

        # Should return without error
        await ch._on_voice(update, None)
        assert bus.inbound.qsize() == 0
