from __future__ import annotations

import json
import time
from pathlib import Path


from claude_bridge.cron.types import CronJob, CronSchedule, CronStore


# ── CronSchedule ──────────────────────────────────────────────


class TestCronSchedule:
    def test_at_schedule_to_dict(self):
        s = CronSchedule(kind="at", at_ms=1700000000000)
        d = s.to_dict()
        assert d == {"kind": "at", "atMs": 1700000000000}

    def test_every_schedule_to_dict(self):
        s = CronSchedule(kind="every", every_ms=60000)
        d = s.to_dict()
        assert d == {"kind": "every", "everyMs": 60000}

    def test_cron_schedule_to_dict(self):
        s = CronSchedule(kind="cron", expr="*/5 * * * *", tz="Asia/Shanghai")
        d = s.to_dict()
        assert d == {"kind": "cron", "expr": "*/5 * * * *", "tz": "Asia/Shanghai"}

    def test_cron_schedule_to_dict_no_tz(self):
        s = CronSchedule(kind="cron", expr="0 9 * * *")
        d = s.to_dict()
        assert d == {"kind": "cron", "expr": "0 9 * * *"}

    def test_at_schedule_from_dict(self):
        s = CronSchedule.from_dict({"kind": "at", "atMs": 1700000000000})
        assert s.kind == "at"
        assert s.at_ms == 1700000000000

    def test_every_schedule_from_dict(self):
        s = CronSchedule.from_dict({"kind": "every", "everyMs": 60000})
        assert s.kind == "every"
        assert s.every_ms == 60000

    def test_cron_schedule_from_dict(self):
        s = CronSchedule.from_dict(
            {"kind": "cron", "expr": "*/5 * * * *", "tz": "Asia/Shanghai"}
        )
        assert s.kind == "cron"
        assert s.expr == "*/5 * * * *"
        assert s.tz == "Asia/Shanghai"

    def test_roundtrip(self):
        original = CronSchedule(kind="every", every_ms=30000)
        restored = CronSchedule.from_dict(original.to_dict())
        assert restored == original


# ── CronJob ───────────────────────────────────────────────────


class TestCronJob:
    def _make_job(self, **overrides) -> CronJob:
        defaults = dict(
            id="job-1",
            name="test job",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
            channel="telegram",
            chat_id="123",
        )
        defaults.update(overrides)
        return CronJob(**defaults)

    def test_defaults(self):
        job = self._make_job()
        assert job.enabled is True
        assert job.delete_after_run is False
        assert job.last_run_at_ms is None
        assert job.next_run_at_ms is None

    def test_to_dict_camel_case(self):
        job = self._make_job(last_run_at_ms=100, next_run_at_ms=200)
        d = job.to_dict()
        assert d["id"] == "job-1"
        assert d["name"] == "test job"
        assert d["chatId"] == "123"
        assert d["deleteAfterRun"] is False
        assert d["lastRunAtMs"] == 100
        assert d["nextRunAtMs"] == 200
        assert isinstance(d["schedule"], dict)
        assert d["schedule"]["kind"] == "every"

    def test_from_dict(self):
        d = {
            "id": "job-2",
            "name": "reminder",
            "schedule": {"kind": "at", "atMs": 9999},
            "message": "wake up",
            "channel": "feishu",
            "chatId": "ou_1",
            "enabled": False,
            "deleteAfterRun": True,
        }
        job = CronJob.from_dict(d)
        assert job.id == "job-2"
        assert job.schedule.kind == "at"
        assert job.schedule.at_ms == 9999
        assert job.chat_id == "ou_1"
        assert job.enabled is False
        assert job.delete_after_run is True
        assert job.last_run_at_ms is None

    def test_roundtrip(self):
        original = self._make_job(
            last_run_at_ms=500, next_run_at_ms=600, delete_after_run=True
        )
        restored = CronJob.from_dict(original.to_dict())
        assert restored == original


# ── CronStore ─────────────────────────────────────────────────


class TestCronStore:
    def test_save_and_load(self, tmp_path: Path):
        path = tmp_path / "jobs.json"
        store = CronStore(path=path)
        job = CronJob(
            id="j1",
            name="ping",
            schedule=CronSchedule(kind="every", every_ms=1000),
            message="ping",
            channel="telegram",
            chat_id="42",
        )
        store.add(job)
        store.save()

        store2 = CronStore(path=path)
        store2.load()
        assert len(store2.jobs) == 1
        assert store2.jobs[0].id == "j1"
        assert store2.jobs[0].schedule.every_ms == 1000

    def test_load_missing_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        store = CronStore(path=path)
        store.load()
        assert store.jobs == []

    def test_add_and_remove(self, tmp_path: Path):
        store = CronStore(path=tmp_path / "jobs.json")
        job1 = CronJob(
            id="a",
            name="a",
            schedule=CronSchedule(kind="every", every_ms=1000),
            message="a",
            channel="t",
            chat_id="1",
        )
        job2 = CronJob(
            id="b",
            name="b",
            schedule=CronSchedule(kind="every", every_ms=2000),
            message="b",
            channel="t",
            chat_id="2",
        )
        store.add(job1)
        store.add(job2)
        assert len(store.jobs) == 2

        removed = store.remove("a")
        assert removed is True
        assert len(store.jobs) == 1
        assert store.jobs[0].id == "b"

        removed = store.remove("nonexistent")
        assert removed is False

    def test_atomic_write(self, tmp_path: Path):
        """save() should use atomic write (tmp + rename) so partial writes don't corrupt."""
        path = tmp_path / "jobs.json"
        store = CronStore(path=path)
        job = CronJob(
            id="x",
            name="x",
            schedule=CronSchedule(kind="at", at_ms=999),
            message="x",
            channel="t",
            chat_id="1",
        )
        store.add(job)
        store.save()

        # File should be valid JSON
        data = json.loads(path.read_text())
        assert isinstance(data, list)
        assert len(data) == 1

    def test_has_changed_detects_external_write(self, tmp_path: Path):
        path = tmp_path / "jobs.json"
        store = CronStore(path=path)
        store.save()

        assert store.has_changed() is False

        # Simulate external modification
        time.sleep(0.05)
        path.write_text("[]")

        assert store.has_changed() is True

    def test_has_changed_false_after_own_save(self, tmp_path: Path):
        path = tmp_path / "jobs.json"
        store = CronStore(path=path)
        store.save()
        assert store.has_changed() is False

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "sub" / "dir" / "jobs.json"
        store = CronStore(path=path)
        store.save()
        assert path.exists()
