from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import OrderedDict
from typing import Any

from uclaw.bus import MessageBus
from uclaw.channels.base import BaseChannel

logger = logging.getLogger(__name__)

_DEDUP_MAX = 1000


class FeishuChannel(BaseChannel):
    """Feishu/Lark channel using WebSocket long connection (no public IP needed)."""

    name = "feishu"

    def __init__(
        self,
        bus: MessageBus,
        app_id: str,
        app_secret: str,
        allowed_users: list[str],
    ) -> None:
        super().__init__(bus, allowed_users)
        self.app_id = app_id
        self.app_secret = app_secret
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        import lark_oapi as lark

        self._running = True
        self._loop = asyncio.get_running_loop()

        # API client for sending messages
        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        # Event handler for incoming messages
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )

        # WebSocket client (long connection)
        self._ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # lark.ws.Client.start() creates its own event loop, so it must
        # run in a separate daemon thread.  We patch the module-level
        # ``loop`` variable so the SDK picks up a fresh loop instead of
        # the already-running main asyncio loop.
        def _run_ws() -> None:
            import time

            import lark_oapi.ws.client as _lark_ws_mod

            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws_mod.loop = ws_loop
            try:
                while self._running:
                    try:
                        self._ws_client.start()
                    except Exception:
                        logger.exception("feishu websocket error")
                    if self._running:
                        time.sleep(5)
            finally:
                ws_loop.close()

        self._ws_thread = threading.Thread(target=_run_ws, daemon=True)
        self._ws_thread.start()
        logger.info("feishu channel started (websocket)")

    async def stop(self) -> None:
        self._running = False
        logger.info("feishu channel stopped")

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def _on_message_sync(self, data: Any) -> None:
        """Sync callback from the WS thread; bridges into the main loop."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: Any) -> None:
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # Dedup
            message_id = message.message_id
            if message_id in self._seen:
                return
            self._seen[message_id] = None
            while len(self._seen) > _DEDUP_MAX:
                self._seen.popitem(last=False)

            # Skip bot messages
            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_type = message.chat_type

            # Parse content JSON
            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            text = content_json.get("text", "")
            if not text:
                return

            # P2P → use sender open_id; group → use chat_id
            chat_id = message.chat_id if chat_type == "group" else sender_id

            await self._handle_message(sender_id, chat_id, text)

        except Exception:
            logger.exception("error processing feishu message")

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send(
        self, chat_id: str, text: str, media: list[str] | None = None
    ) -> None:
        if self._client is None:
            logger.warning("feishu client not initialized, cannot send")
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send_text_sync, chat_id, text)

    def _send_text_sync(self, chat_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"
        content = json.dumps({"text": text}, ensure_ascii=False)

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "feishu send failed: code=%s msg=%s",
                    response.code,
                    response.msg,
                )
        except Exception:
            logger.exception("feishu send error")
