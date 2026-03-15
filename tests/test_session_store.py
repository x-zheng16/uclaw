from __future__ import annotations

import json

import pytest

from claude_bridge.router import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.json")


def test_get_returns_none_for_missing_key(store):
    assert store.get("telegram:123") is None


def test_set_and_get(store):
    store.set("telegram:123", "session-abc")
    assert store.get("telegram:123") == "session-abc"


def test_remove(store):
    store.set("telegram:123", "session-abc")
    store.remove("telegram:123")
    assert store.get("telegram:123") is None


def test_remove_missing_key_is_noop(store):
    store.remove("telegram:999")  # should not raise


def test_save_creates_file(store):
    store.set("telegram:1", "s1")
    store.save()
    assert store._path.exists()
    data = json.loads(store._path.read_text())
    assert data == {"telegram:1": "s1"}


def test_load_restores_data(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"feishu:ou_1": "session-xyz"}))
    store = SessionStore(path)
    store.load()
    assert store.get("feishu:ou_1") == "session-xyz"


def test_load_missing_file_is_noop(store):
    store.load()  # should not raise
    assert store.get("any") is None


def test_save_atomic_replaces_existing(tmp_path):
    path = tmp_path / "sessions.json"
    store = SessionStore(path)
    store.set("k1", "v1")
    store.save()
    store.set("k2", "v2")
    store.save()
    data = json.loads(path.read_text())
    assert data == {"k1": "v1", "k2": "v2"}


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "sessions.json"
    store1 = SessionStore(path)
    store1.set("a:1", "s1")
    store1.set("b:2", "s2")
    store1.save()

    store2 = SessionStore(path)
    store2.load()
    assert store2.get("a:1") == "s1"
    assert store2.get("b:2") == "s2"
