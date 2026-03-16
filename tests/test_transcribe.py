from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from uclaw.transcribe import transcribe, _model_cache


class TestTranscribeFallback:
    """When faster_whisper is not installed, return a fallback string."""

    @pytest.mark.asyncio
    async def test_fallback_when_no_whisper(self):
        with patch("uclaw.transcribe._try_import_whisper", return_value=None):
            # Clear any cached model
            _model_cache.clear()
            result = await transcribe("/tmp/test.ogg")
        assert "[Voice message:" in result
        assert "/tmp/test.ogg" in result


class TestTranscribeWithWhisper:
    """When faster_whisper is available, use it to transcribe."""

    @pytest.mark.asyncio
    async def test_transcribes_with_whisper(self):
        fake_segment = MagicMock()
        fake_segment.text = " Hello world"

        fake_model = MagicMock()
        fake_model.transcribe.return_value = ([fake_segment], None)

        fake_module = MagicMock()
        fake_module.WhisperModel.return_value = fake_model

        with patch("uclaw.transcribe._try_import_whisper", return_value=fake_module):
            _model_cache.clear()
            result = await transcribe("/tmp/test.ogg")

        assert result == "Hello world"
        fake_model.transcribe.assert_called_once_with("/tmp/test.ogg", beam_size=1)

    @pytest.mark.asyncio
    async def test_model_is_cached(self):
        fake_segment = MagicMock()
        fake_segment.text = " Hi"

        fake_model = MagicMock()
        fake_model.transcribe.return_value = ([fake_segment], None)

        fake_module = MagicMock()
        fake_module.WhisperModel.return_value = fake_model

        with patch("uclaw.transcribe._try_import_whisper", return_value=fake_module):
            _model_cache.clear()
            await transcribe("/tmp/a.ogg")
            await transcribe("/tmp/b.ogg")

        # WhisperModel should only be constructed once (cached)
        assert fake_module.WhisperModel.call_count == 1
        assert fake_model.transcribe.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_transcription(self):
        fake_model = MagicMock()
        fake_model.transcribe.return_value = ([], None)

        fake_module = MagicMock()
        fake_module.WhisperModel.return_value = fake_model

        with patch("uclaw.transcribe._try_import_whisper", return_value=fake_module):
            _model_cache.clear()
            result = await transcribe("/tmp/empty.ogg")

        assert result == ""

    @pytest.mark.asyncio
    async def test_strips_leading_whitespace(self):
        seg1 = MagicMock()
        seg1.text = "  Hello "
        seg2 = MagicMock()
        seg2.text = " world  "

        fake_model = MagicMock()
        fake_model.transcribe.return_value = ([seg1, seg2], None)

        fake_module = MagicMock()
        fake_module.WhisperModel.return_value = fake_model

        with patch("uclaw.transcribe._try_import_whisper", return_value=fake_module):
            _model_cache.clear()
            result = await transcribe("/tmp/multi.ogg")

        assert result == "Hello  world"
