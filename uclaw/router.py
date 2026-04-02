from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from threading import Lock

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from uclaw.bus import InboundMessage, MessageBus, OutboundMessage
from uclaw.config import BridgeConfig

logger = logging.getLogger(__name__)

QUERY_TIMEOUT = 600  # 10 minutes


class HistoryLogger:
    """Append inbound prompts to ~/.uclaw/history.jsonl (mirrors CC history format)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def log(self, msg: InboundMessage, session_id: str | None, project: str) -> None:
        entry = {
            "display": msg.text,
            "pastedContents": {},
            "timestamp": int(time.time() * 1000),
            "project": project,
            "sessionId": session_id or "",
            "channel": msg.channel,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)


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
        self._history = HistoryLogger(data_dir / "history.jsonl")
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending_inputs: dict[str, list[InboundMessage]] = {}
        self._started_at = time.monotonic()

    async def warm_up(self) -> None:
        """Pre-create sessions for all known session keys."""
        keys = list(self._store._data.keys())
        if not keys:
            return
        logger.info("Warming up %d session(s)...", len(keys))
        tasks = [self._create_session(key) for key in keys]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                logger.warning("Failed to warm up session %s: %s", key, result)
            else:
                logger.info("Warmed up session %s", key)

    async def run(self) -> None:
        """Main loop: consume inbound messages and route to sessions."""
        logger.info("SessionRouter started")
        while True:
            msg = await self._bus.consume_inbound()
            asyncio.create_task(self._dispatch(msg))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Handle a single message, serialized per session key via asyncio.Lock.

        If the session is busy, queue the message and send an ACK.  After the
        current turn completes the lock-holder drains all queued messages into
        one concatenated follow-up turn so Claude sees them together.
        """
        key = msg.session_key
        lock = self._locks.setdefault(key, asyncio.Lock())

        if lock.locked() and not msg.text.startswith("/"):
            # Atomically queue (no await between check and append — asyncio safe)
            self._pending_inputs.setdefault(key, []).append(msg)
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    text="[queued] noted, will address once current task finishes",
                )
            )
            return  # Don't compete for the lock — let the holder drain us

        async with lock:
            await self._process(msg)
            # Drain any messages that piled up while we held the lock.
            # Loop: processing the batch may itself attract new queued messages.
            while True:
                queued = self._pending_inputs.pop(key, [])
                if not queued:
                    break
                texts = "\n".join(f"{i + 1}. {m.text}" for i, m in enumerate(queued))
                batch_text = (
                    f"While finishing that task, I also sent the following "
                    f"{'message' if len(queued) == 1 else f'{len(queued)} messages'}. "
                    f"Please address {'it' if len(queued) == 1 else 'all of them'}:\n{texts}"
                )
                # Reconstruct as an inbound message from the same sender
                ref = queued[0]
                batch_msg = InboundMessage(
                    channel=ref.channel,
                    chat_id=ref.chat_id,
                    sender_id=ref.sender_id,
                    text=batch_text,
                )
                await self._process(batch_msg)

    async def _process(self, msg: InboundMessage) -> None:
        self._history.log(
            msg,
            session_id=self._store.get(msg.session_key),
            project=str(Path(self._config.claude.workspace).expanduser()),
        )
        try:
            if msg.text.startswith("/"):
                await self._handle_command(msg)
            else:
                await self._handle_message(msg)
        except Exception:
            logger.exception("Error handling message from %s", msg.session_key)

    async def _handle_command(self, msg: InboundMessage) -> None:
        cmd = msg.text.strip().split()[0].lower()
        if cmd == "/help":
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    text=(
                        "uclaw commands\n"
                        "\n"
                        "-- uclaw layer (bypass CC) --\n"
                        "/help    show this message\n"
                        "/status  uptime + active sessions\n"
                        "/new     kill & restart Claude session (hard reset + free memory)\n"
                        "/stop    interrupt current run (like pressing Esc in terminal)\n"
                        "/tmux    list tmux sessions\n"
                        "/tmux <session>          list windows\n"
                        "/tmux <session> <window> list panes\n"
                        "\n"
                        "-- forwarded to Claude Code --\n"
                        "/clear /reset /compact /cost /memory /doctor ...\n"
                        "any unknown /cmd is forwarded to CC as-is"
                    ),
                )
            )
        elif cmd == "/status":
            uptime_s = int(time.monotonic() - self._started_at)
            h, rem = divmod(uptime_s, 3600)
            m, s = divmod(rem, 60)
            uptime_str = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
            sessions = list(self._clients.keys())
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    text=(
                        "[uclaw status]\n"
                        + f"uptime: {uptime_str}\n"
                        + f"active sessions: {len(sessions)}\n"
                        + (
                            "\n".join(f"  {k}" for k in sessions)
                            if sessions
                            else "  none"
                        )
                    ),
                )
            )
        elif cmd == "/new":
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
        elif cmd == "/tmux":
            await self._handle_tmux(msg)
        else:
            # Forward unknown commands to Claude Code as-is
            await self._handle_message(msg)

    async def _handle_tmux(self, msg: InboundMessage) -> None:
        import subprocess

        parts = msg.text.strip().split()
        try:
            if len(parts) == 1:
                result = subprocess.run(
                    ["tmux", "list-sessions"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            elif len(parts) == 2:
                result = subprocess.run(
                    ["tmux", "list-windows", "-t", parts[1]],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            else:
                target = parts[1] + ":" + parts[2]
                result = subprocess.run(
                    [
                        "tmux",
                        "list-panes",
                        "-t",
                        target,
                        "-F",
                        "#{pane_index} #{pane_current_command} #{pane_current_path}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        except Exception as exc:
            output = "[tmux error] " + str(exc)
        await self._bus.publish_outbound(
            OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, text=output)
        )

    async def _handle_message(self, msg: InboundMessage) -> None:
        key = msg.session_key
        client = self._clients.get(key)
        if client is None:
            client = await self._create_session(key)

        query_preview = msg.text[:80].replace("\n", " ")
        logger.info("[%s] query: %s", key, query_preview)

        typing_task = asyncio.create_task(self._typing_loop(msg))
        try:
            try:
                try:
                    await asyncio.wait_for(
                        client.query(msg.text), timeout=QUERY_TIMEOUT
                    )
                except Exception:
                    logger.warning("Query failed for %s, reconnecting...", key)
                    self._clients.pop(key, None)
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    client = await self._create_session(key)
                    await asyncio.wait_for(
                        client.query(msg.text), timeout=QUERY_TIMEOUT
                    )

                async for outbound in self._collect_response(client, msg):
                    await self._bus.publish_outbound(outbound)
            except Exception as exc:
                logger.exception("Message handling failed for %s", key)
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text=f"[error] {type(exc).__name__}: {exc}",
                    )
                )
        finally:
            typing_task.cancel()

    async def _typing_loop(self, msg: InboundMessage) -> None:
        """Send typing indicator every 4s until cancelled."""
        try:
            while True:
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, text="", kind="typing"
                    )
                )
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def _create_session(self, key: str) -> ClaudeSDKClient:
        cc = self._config.claude
        resume_id = self._store.get(key)
        options = ClaudeAgentOptions(
            cwd=Path(cc.workspace).expanduser(),
            permission_mode=cc.permission_mode,
            setting_sources=cc.setting_sources,
            resume=resume_id,
            cli_path=cc.cli_path,
        )
        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._clients[key] = client
        logger.info("Created session for %s (resume=%s)", key, resume_id)
        if resume_id:
            await self._drain_stale_messages(client, key)
        return client

    async def _drain_stale_messages(
        self, client: ClaudeSDKClient, key: str
    ) -> None:
        """Drain ALL buffered startup messages from CC history replay on --resume.

        CC may replay multiple prior turns (AssistantMessage + ResultMessage per turn).
        We must drain every replayed ResultMessage, not just the first, otherwise
        leftover turns sit in the buffer and get served as responses to future queries
        causing a "shifted by N" desync.

        Strategy: keep draining turn-by-turn until 1 second of silence (no more
        replayed messages). Each inner loop consumes one turn (up to its ResultMessage).
        """
        drained = 0
        try:
            while True:
                try:
                    async with asyncio.timeout(1.0):
                        async for msg in client.receive_messages():
                            drained += 1
                            logger.debug(
                                "Drained startup message for %s: %s",
                                key,
                                type(msg).__name__,
                            )
                            if isinstance(msg, ResultMessage):
                                break  # end of one replayed turn; loop to drain next
                except (TimeoutError, asyncio.TimeoutError):
                    break  # 1 s silence → no more replayed turns
        except Exception:
            logger.debug("drain error for %s (non-fatal)", key, exc_info=True)
        if drained:
            logger.warning(
                "Drained %d stale message(s) from %s buffer", drained, key
            )

    async def _disconnect_session(self, key: str) -> None:
        client = self._clients.pop(key, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Error disconnecting session %s (non-fatal)", key, exc_info=True)
        self._store.remove(key)
        self._store.save()

    async def _collect_response(self, client: ClaudeSDKClient, msg: InboundMessage):
        """Iterate response stream, yield OutboundMessages for text blocks."""
        key = msg.session_key
        async for response in client.receive_response():
            if isinstance(response, AssistantMessage):
                text_parts = [
                    block.text
                    for block in response.content
                    if isinstance(block, TextBlock)
                ]
                full_text = "".join(text_parts).strip()
                if full_text:
                    reply_preview = full_text[:80].replace("\n", " ")
                    logger.info("[%s] reply: %s", key, reply_preview)
                    yield OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        text=full_text,
                    )
            elif isinstance(response, ResultMessage):
                # Persist session_id for resume
                self._store.set(msg.session_key, response.session_id)
                self._store.save()
                logger.info(
                    "[%s] result: turns=%d cost=$%.4f error=%s",
                    key,
                    response.num_turns,
                    response.total_cost_usd or 0,
                    response.is_error,
                )
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
                    logger.debug("Error disconnecting %s during shutdown (non-fatal)", key, exc_info=True)
        self._store.save()
        logger.info("SessionRouter shut down, %d sessions saved", len(keys))
