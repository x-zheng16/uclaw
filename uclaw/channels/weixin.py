from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from base64 import b64encode
from pathlib import Path

import aiohttp

from uclaw.bus import MessageBus
from uclaw.channels.base import BaseChannel

logger = logging.getLogger(__name__)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
LONG_POLL_TIMEOUT = 35
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY = 30
RETRY_DELAY = 2
SESSION_EXPIRED_ERRCODE = -14
MAX_MESSAGE_LEN = 4000


def _random_wechat_uin() -> str:
    uint32 = int.from_bytes(os.urandom(4), "big")
    return b64encode(str(uint32).encode()).decode()


def _split_message(text: str, max_len: int = MAX_MESSAGE_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        else:
            cut += 1
        parts.append(text[:cut])
        text = text[cut:]
    return parts


class WeixinChannel(BaseChannel):
    name = "weixin"

    def __init__(
        self,
        bus: MessageBus,
        base_url: str,
        allowed_users: list[str],
        data_dir: Path,
    ) -> None:
        super().__init__(bus, allowed_users)
        self._base_url = base_url.rstrip("/")
        self._data_dir = data_dir
        self._token: str | None = None
        self._account_id: str | None = None
        self._get_updates_buf: str = ""
        self._context_tokens: dict[str, str] = {}
        self._session: aiohttp.ClientSession | None = None

    # -- persistence --------------------------------------------------------

    def _account_path(self) -> Path:
        return self._data_dir / "account.json"

    def _sync_buf_path(self) -> Path:
        return self._data_dir / "sync_buf.txt"

    def _load_account(self) -> None:
        path = self._account_path()
        if path.exists():
            data = json.loads(path.read_text())
            self._token = data.get("token")
            self._account_id = data.get("account_id")
            base = data.get("base_url")
            if base:
                self._base_url = base.rstrip("/")
        buf_path = self._sync_buf_path()
        if buf_path.exists():
            self._get_updates_buf = buf_path.read_text().strip()

    def _save_account(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._account_path()
        data = {
            "token": self._token,
            "account_id": self._account_id,
            "base_url": self._base_url,
        }
        path.write_text(json.dumps(data, indent=2))
        path.chmod(0o600)

    def _save_sync_buf(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._sync_buf_path().write_text(self._get_updates_buf)

    # -- HTTP helpers -------------------------------------------------------

    def _auth_headers(self, body: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        if body is not None:
            headers["Content-Length"] = str(len(body.encode()))
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _api_post(
        self, endpoint: str, payload: dict, timeout: int = 15
    ) -> dict:
        assert self._session is not None
        url = f"{self._base_url}/{endpoint}"
        body = json.dumps(payload)
        async with self._session.post(
            url,
            data=body,
            headers=self._auth_headers(body),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            if not resp.ok:
                raise RuntimeError(f"{endpoint} {resp.status}: {text}")
            return json.loads(text)

    async def _api_get(
        self, endpoint: str, params: dict | None = None, timeout: int = 15
    ) -> dict:
        assert self._session is not None
        url = f"{self._base_url}/{endpoint}"
        headers = {"iLink-App-ClientVersion": "1"}
        async with self._session.get(
            url,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            if not resp.ok:
                raise RuntimeError(f"{endpoint} {resp.status}: {text}")
            return json.loads(text)

    # -- QR login -----------------------------------------------------------

    async def _login(self) -> None:
        logger.info("weixin: starting QR login...")
        resp = await self._api_get(
            "ilink/bot/get_bot_qrcode",
            params={"bot_type": BOT_TYPE},
        )
        qrcode_ticket = resp["qrcode"]
        qrcode_url = resp["qrcode_img_content"]

        print("\n" + "=" * 50)
        print("Scan with WeChat to connect:")
        print("=" * 50)
        try:
            import qrcode as qr_lib

            qr = qr_lib.QRCode(border=1)
            qr.add_data(qrcode_url)
            qr.print_ascii(invert=True)
        except ImportError:
            print(f"QR URL: {qrcode_url}")
            print("(install 'qrcode' for terminal QR display)")
        print("=" * 50 + "\n")

        deadline = asyncio.get_event_loop().time() + 480
        scanned_logged = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                status = await self._api_get(
                    "ilink/bot/get_qrcode_status",
                    params={"qrcode": qrcode_ticket},
                    timeout=40,
                )
            except (asyncio.TimeoutError, aiohttp.ClientError):
                continue

            match status.get("status"):
                case "wait":
                    pass
                case "scaned":
                    if not scanned_logged:
                        logger.info("weixin: QR scanned, waiting for confirmation...")
                        print("QR scanned, confirm on your phone...")
                        scanned_logged = True
                case "confirmed":
                    self._token = status["bot_token"]
                    self._account_id = status["ilink_bot_id"]
                    if status.get("baseurl"):
                        self._base_url = status["baseurl"].rstrip("/")
                    self._save_account()
                    logger.info(
                        "weixin: login successful! account_id=%s", self._account_id
                    )
                    print("WeChat connected!\n")
                    return
                case "expired":
                    raise RuntimeError("QR code expired, please restart")

            await asyncio.sleep(1)

        raise RuntimeError("Login timed out (8 min)")

    # -- long-poll loop -----------------------------------------------------

    async def _poll_loop(self) -> None:
        consecutive_failures = 0
        while self._running:
            try:
                resp = await self._api_post(
                    "ilink/bot/getupdates",
                    {
                        "get_updates_buf": self._get_updates_buf,
                        "base_info": {"channel_version": "uclaw-1.0"},
                    },
                    timeout=LONG_POLL_TIMEOUT + 5,
                )

                ret = resp.get("ret", 0)
                errcode = resp.get("errcode", 0)
                if ret != 0 or errcode != 0:
                    consecutive_failures += 1
                    logger.error(
                        "weixin getupdates: ret=%s errcode=%s errmsg=%s (%d/%d)",
                        ret,
                        errcode,
                        resp.get("errmsg", ""),
                        consecutive_failures,
                        MAX_CONSECUTIVE_FAILURES,
                    )
                    if errcode == SESSION_EXPIRED_ERRCODE:
                        logger.error("weixin: session expired, re-login required")
                        self._token = None
                        await self._login()
                        consecutive_failures = 0
                        continue
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0
                        await asyncio.sleep(BACKOFF_DELAY)
                    else:
                        await asyncio.sleep(RETRY_DELAY)
                    continue

                consecutive_failures = 0

                new_buf = resp.get("get_updates_buf", "")
                if new_buf:
                    self._get_updates_buf = new_buf
                    self._save_sync_buf()

                for msg in resp.get("msgs", []):
                    await self._process_inbound(msg)

            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                continue
            except Exception:
                consecutive_failures += 1
                logger.exception(
                    "weixin poll error (%d/%d)",
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                    await asyncio.sleep(BACKOFF_DELAY)
                else:
                    await asyncio.sleep(RETRY_DELAY)

    async def _process_inbound(self, msg: dict) -> None:
        from_user = msg.get("from_user_id", "")
        if not from_user:
            return

        # Only process user messages (message_type=1), skip bot echoes
        if msg.get("message_type", 0) != 1:
            return

        ctx_token = msg.get("context_token")
        if ctx_token:
            self._context_tokens[from_user] = ctx_token

        text = self._extract_text(msg)
        if not text:
            return

        logger.info("weixin: message from %s: %s", from_user, text[:80])
        await self._handle_message(from_user, from_user, text)

    @staticmethod
    def _extract_text(msg: dict) -> str:
        for item in msg.get("item_list", []):
            item_type = item.get("type", 0)
            if item_type == 1:  # TEXT
                text = item.get("text_item", {}).get("text", "")
                ref = item.get("ref_msg")
                if ref and ref.get("title"):
                    text = f"[ref: {ref['title']}]\n{text}"
                return text
            if item_type == 3:  # VOICE with transcription
                voice_text = item.get("voice_item", {}).get("text")
                if voice_text:
                    return voice_text
        return ""

    # -- public interface ---------------------------------------------------

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._load_account()
        if not self._token:
            await self._login()
        logger.info("weixin channel started (account=%s)", self._account_id)
        await self._poll_loop()

    async def stop(self) -> None:
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def send(
        self, chat_id: str, text: str, media: list[str] | None = None
    ) -> None:
        if self._session is None:
            logger.warning("weixin: session not initialized, cannot send")
            return

        context_token = self._context_tokens.get(chat_id)
        if not context_token:
            logger.warning("weixin: no context_token for %s, cannot reply", chat_id)
            return

        for part in _split_message(text):
            client_id = f"uclaw-{secrets.token_hex(8)}"
            payload = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": chat_id,
                    "client_id": client_id,
                    "message_type": 2,  # BOT
                    "message_state": 2,  # FINISH
                    "item_list": [{"type": 1, "text_item": {"text": part}}],
                    "context_token": context_token,
                },
                "base_info": {"channel_version": "uclaw-1.0"},
            }
            try:
                await self._api_post("ilink/bot/sendmessage", payload)
            except Exception:
                logger.exception("weixin: failed to send to %s", chat_id)
