from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from uclaw.bus import InboundMessage
from uclaw.router import HistoryLogger


@pytest.fixture
def logger(tmp_path):
    return HistoryLogger(tmp_path / "history.jsonl")


def _make_msg(text: str = "hello", channel: str = "weixin", chat_id: str = "user123") -> InboundMessage:
    return InboundMessage(channel=channel, chat_id=chat_id, sender_id="s1", text=text)


def _read_entries(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_log_creates_file(logger, tmp_path):
    msg = _make_msg()
    logger.log(msg, session_id="sess-1", project="/home/xiang/.uclaw")
    assert (tmp_path / "history.jsonl").exists()


def test_log_entry_fields(logger, tmp_path):
    msg = _make_msg(text="hi there", channel="telegram")
    before = int(time.time() * 1000)
    logger.log(msg, session_id="sess-abc", project="/workspace")
    after = int(time.time() * 1000)

    entries = _read_entries(tmp_path / "history.jsonl")
    assert len(entries) == 1
    e = entries[0]
    assert e["display"] == "hi there"
    assert e["pastedContents"] == {}
    assert before <= e["timestamp"] <= after
    assert e["project"] == "/workspace"
    assert e["sessionId"] == "sess-abc"
    assert e["channel"] == "telegram"


def test_log_none_session_id(logger, tmp_path):
    msg = _make_msg()
    logger.log(msg, session_id=None, project="/p")
    entries = _read_entries(tmp_path / "history.jsonl")
    assert entries[0]["sessionId"] == ""


def test_log_appends_multiple_entries(logger, tmp_path):
    for i in range(3):
        logger.log(_make_msg(text=f"msg{i}"), session_id=f"s{i}", project="/p")
    entries = _read_entries(tmp_path / "history.jsonl")
    assert len(entries) == 3
    assert [e["display"] for e in entries] == ["msg0", "msg1", "msg2"]


def test_log_creates_parent_dirs(tmp_path):
    nested = tmp_path / "a" / "b" / "history.jsonl"
    lg = HistoryLogger(nested)
    lg.log(_make_msg(), session_id="s", project="/p")
    assert nested.exists()


def test_log_unicode(logger, tmp_path):
    msg = _make_msg(text="你好世界 🌏")
    logger.log(msg, session_id="s", project="/p")
    entries = _read_entries(tmp_path / "history.jsonl")
    assert entries[0]["display"] == "你好世界 🌏"


def test_log_one_entry_per_line(logger, tmp_path):
    logger.log(_make_msg(text="a"), session_id="s1", project="/p")
    logger.log(_make_msg(text="b"), session_id="s2", project="/p")
    lines = (tmp_path / "history.jsonl").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line must be valid JSON


def test_log_different_channels(logger, tmp_path):
    logger.log(_make_msg(channel="telegram"), session_id="s1", project="/p")
    logger.log(_make_msg(channel="feishu"), session_id="s2", project="/p")
    logger.log(_make_msg(channel="weixin"), session_id="s3", project="/p")
    entries = _read_entries(tmp_path / "history.jsonl")
    assert [e["channel"] for e in entries] == ["telegram", "feishu", "weixin"]
