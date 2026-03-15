from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from claude_bridge.bus import InboundMessage, MessageBus, OutboundMessage
from claude_bridge.config import BridgeConfig

logger = logging.getLogger(__name__)

QUERY_TIMEOUT = 600  # 10 minutes


class SessionStore:
    """Persist {channel}:{chat_id} -> claude_session_id mapping to disk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, session_id: str) -> None:
        self._data[key] = session_id

    def remove(self, key: str) -> None:
        self._data.pop(key, None)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with open(tmp_fd, "w") as f:
                json.dump(self._data, f)
            Path(tmp_path).replace(self._path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def load(self) -> None:
        if not self._path.exists():
            return
        self._data = json.loads(self._path.read_text())


class SessionRouter:
    """Route inbound messages to persistent ClaudeSDKClient sessions."""

    def __init__(self, config: BridgeConfig, bus: MessageBus, data_dir: Path) -> None:
        self._config = config
        self._bus = bus
        self._store = SessionStore(data_dir / "sessions.json")
        self._store.load()
        self._clients: dict[str, ClaudeSDKClient] = {}

    async def run(self) -> None:
        """Main loop: consume inbound messages and route to sessions."""
        logger.info("SessionRouter started")
        while True:
            msg = await self._bus.consume_inbound()
            try:
                if msg.text.startswith("/"):
                    await self._handle_command(msg)
                else:
                    await self._handle_message(msg)
            except Exception:
                logger.exception("Error handling message from %s", msg.session_key)

    async def _handle_command(self, msg: InboundMessage) -> None:
        cmd = msg.text.strip().split()[0].lower()
        if cmd == "/new":
            await self._disconnect_session(msg.session_key)
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    text="Session reset. Send a message to start a new conversation.",
                )
            )
        elif cmd == "/stop":
            client = self._clients.get(msg.session_key)
            if client:
                await client.interrupt()
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text="Interrupted.",
                    )
                )

    async def _handle_message(self, msg: InboundMessage) -> None:
        key = msg.session_key
        client = self._clients.get(key)
        if client is None:
            client = await self._create_session(key)

        await asyncio.wait_for(client.query(msg.text), timeout=QUERY_TIMEOUT)

        async for outbound in self._collect_response(client, msg):
            await self._bus.publish_outbound(outbound)

    async def _create_session(self, key: str) -> ClaudeSDKClient:
        cc = self._config.claude
        resume_id = self._store.get(key)
        options = ClaudeAgentOptions(
            cwd=Path(cc.workspace).expanduser(),
            permission_mode=cc.permission_mode,
            setting_sources=cc.setting_sources,
            resume=resume_id,
        )
        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._clients[key] = client
        logger.info("Created session for %s (resume=%s)", key, resume_id)
        return client

    async def _disconnect_session(self, key: str) -> None:
        client = self._clients.pop(key, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error disconnecting session %s", key)
        self._store.remove(key)
        self._store.save()

    async def _collect_response(self, client: ClaudeSDKClient, msg: InboundMessage):
        """Iterate response stream, yield OutboundMessages for text blocks."""
        async for response in client.receive_response():
            if isinstance(response, AssistantMessage):
                text_parts = [
                    block.text
                    for block in response.content
                    if isinstance(block, TextBlock)
                ]
                if text_parts:
                    yield OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text="".join(text_parts),
                    )
            elif isinstance(response, ResultMessage):
                # Persist session_id for resume
                self._store.set(msg.session_key, response.session_id)
                self._store.save()
                if response.is_error:
                    yield OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text=f"[error] {response.result or 'Unknown error'}",
                    )

    async def shutdown(self) -> None:
        """Disconnect all sessions and persist store."""
        keys = list(self._clients.keys())
        for key in keys:
            client = self._clients.pop(key, None)
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception("Error disconnecting %s during shutdown", key)
        self._store.save()
        logger.info("SessionRouter shut down, %d sessions saved", len(keys))
