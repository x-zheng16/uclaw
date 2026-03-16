from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from uclaw.transcribe import transcribe


class TestTranscribeNoKey:
    """When no Groq API key is provided, return a fallback string."""

    @pytest.mark.asyncio
    async def test_fallback_when_no_key(self):
        result = await transcribe("/tmp/test.ogg")
        assert "[Voice message:" in result
        assert "no Groq API key" in result

    @pytest.mark.asyncio
    async def test_fallback_when_empty_key(self):
        result = await transcribe("/tmp/test.ogg", groq_api_key="")
        assert "[Voice message:" in result


class TestTranscribeWithGroq:
    """When Groq API key is provided, call the API."""

    @pytest.mark.asyncio
    async def test_successful_transcription(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": " Hello world "}
        mock_response.raise_for_status = MagicMock()

        with patch("uclaw.transcribe._transcribe_groq", return_value="Hello world"):
            result = await transcribe("/tmp/test.ogg", groq_api_key="fake-key")

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_api_error_returns_fallback(self):
        with patch("uclaw.transcribe._transcribe_groq", side_effect=Exception("API error")):
            result = await transcribe("/tmp/test.ogg", groq_api_key="fake-key")

        assert "[Voice message:" in result
        assert "API error" in result
