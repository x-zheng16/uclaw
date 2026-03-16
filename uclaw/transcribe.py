from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Cache dict so the whisper model is loaded only once.
_model_cache: dict[str, Any] = {}


def _try_import_whisper() -> Any | None:
    """Try to import faster_whisper. Returns the module or None."""
    try:
        import faster_whisper

        return faster_whisper
    except ImportError:
        return None


def _get_or_create_model(fw_module: Any) -> Any:
    """Lazily create and cache the WhisperModel."""
    if "model" not in _model_cache:
        logger.info("loading faster-whisper model (base)...")
        _model_cache["model"] = fw_module.WhisperModel("base")
    return _model_cache["model"]


def _transcribe_sync(file_path: str) -> str:
    """Synchronous transcription. Runs in a thread pool."""
    fw = _try_import_whisper()
    if fw is None:
        return f"[Voice message: {file_path}. Please use Bash to transcribe this file.]"

    model = _get_or_create_model(fw)
    segments, _info = model.transcribe(file_path, beam_size=1)
    text = "".join(seg.text for seg in segments).strip()
    return text


async def transcribe(file_path: str) -> str:
    """Transcribe audio file to text.

    Tries faster-whisper locally, falls back to a placeholder description.
    Runs inference in a thread pool to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_transcribe_sync, file_path)
