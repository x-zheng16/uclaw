from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"


async def transcribe(file_path: str, groq_api_key: str | None = None) -> str:
    """Transcribe audio file to text via Groq Whisper API.

    If no API key is provided, returns a fallback placeholder.
    """
    if not groq_api_key:
        return f"[Voice message: {file_path}. Transcription unavailable — no Groq API key configured.]"

    try:
        return await asyncio.to_thread(_transcribe_groq, file_path, groq_api_key)
    except Exception as e:
        logger.error("Groq transcription failed: %s", e)
        return f"[Voice message: {file_path}. Transcription failed: {e}]"


def _transcribe_groq(file_path: str, api_key: str) -> str:
    """Synchronous Groq Whisper API call. Runs in a thread pool."""
    path = Path(file_path)
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, path.read_bytes(), "audio/ogg")},
            data={"model": GROQ_MODEL},
        )
        resp.raise_for_status()
        return resp.json()["text"].strip()
