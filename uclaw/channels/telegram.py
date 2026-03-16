from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from uclaw.bus import MessageBus
from uclaw.channels.base import BaseChannel
from uclaw.transcribe import transcribe

logger = logging.getLogger(__name__)

MEDIA_DIR = Path.home() / ".uclaw" / "media"


def split_message(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Try to split at last newline within max_len
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        else:
            cut += 1  # include the newline in the current part
        parts.append(text[:cut])
        text = text[cut:]
    return parts


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(
        self, bus: MessageBus, token: str, allowed_users: list[str],
        groq_api_key: str | None = None,
    ) -> None:
        super().__init__(bus, allowed_users)
        self.token = token
        self._groq_api_key = groq_api_key
        self._app: Application | None = None

    async def start(self) -> None:
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        self._app.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram channel started")
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._app is not None:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            if self._app.running:
                await self._app.stop()
            await self._app.shutdown()
            self._app = None

    async def _on_message(self, update: Update, context: object) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        sender_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id) if update.effective_chat else sender_id
        text = update.effective_message.text or ""
        await self._handle_message(sender_id, chat_id, text)

    async def _on_voice(self, update: Update, context: object) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        voice_or_audio = msg.voice or msg.audio
        if voice_or_audio is None:
            return

        sender_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id) if update.effective_chat else sender_id

        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        file_path = str(MEDIA_DIR / f"{voice_or_audio.file_unique_id}.ogg")

        tg_file = await voice_or_audio.get_file()
        await tg_file.download_to_drive(file_path)

        text = await transcribe(file_path, groq_api_key=self._groq_api_key)
        await self._handle_message(sender_id, chat_id, text)

    async def send(
        self, chat_id: str, text: str, media: list[str] | None = None
    ) -> None:
        if self._app is None:
            logger.warning("telegram app not initialized, cannot send")
            return
        for part in split_message(text):
            await self._app.bot.send_message(chat_id=int(chat_id), text=part)
