from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from uclaw.bus import MessageBus
from uclaw.cron.service import CronScheduler
from uclaw.cron.types import CronJob, CronSchedule, CronStore


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_job(
    *,
    id: str = "j1",
    kind: str = "every",
    every_ms: int = 50,
    at_ms: int | None = None,
    message: str = "hello",
    channel: str = "telegram",
    chat_id: str = "42",
    enabled: bool = True,
    delete_after_run: bool = False,
) -> CronJob:
    if kind == "every":
        sched = CronSchedule(kind="every", every_ms=every_ms)
    elif kind == "at":
        sched = CronSchedule(kind="at", at_ms=at_ms)
    else:
        raise ValueError(kind)
    return CronJob(
        id=id,
        name=id,
        schedule=sched,
        message=message,
        channel=channel,
        chat_id=chat_id,
        enabled=enabled,
        delete_after_run=delete_after_run,
    )


class TestComputeNextRun:
    """Test _compute_next_run without running the full scheduler."""

    def test_every_never_ran(self):
        job = _make_job(every_ms=1000)
        scheduler = CronScheduler(
            store=CronStore(path=Path("/dev/null")),
            bus=MessageBus(),
            execute_fn=lambda m, c, ci: asyncio.coroutine(lambda: "ok")(),
        )
        now = _now_ms()
        nxt = scheduler._compute_next_run(job, now)
        assert nxt is not None
        assert abs(nxt - (now + 1000)) < 50

    def test_every_after_last_run(self):
        job = _make_job(every_ms=500)
        job.last_run_at_ms = _now_ms() - 200
        scheduler = CronScheduler(
            store=CronStore(path=Path("/dev/null")),
            bus=MessageBus(),
            execute_fn=lambda m, c, ci: asyncio.coroutine(lambda: "ok")(),
        )
        nxt = scheduler._compute_next_run(job, _now_ms())
        assert nxt is not None
        expected = job.last_run_at_ms + 500
        assert abs(nxt - expected) < 50

    def test_at_future(self):
        future = _now_ms() + 5000
        job = _make_job(kind="at", at_ms=future)
        scheduler = CronScheduler(
            store=CronStore(path=Path("/dev/null")),
            bus=MessageBus(),
            execute_fn=lambda m, c, ci: asyncio.coroutine(lambda: "ok")(),
        )
        nxt = scheduler._compute_next_run(job, _now_ms())
        assert nxt == future

    def test_at_past_returns_none(self):
        past = _now_ms() - 5000
        job = _make_job(kind="at", at_ms=past)
        scheduler = CronScheduler(
            store=CronStore(path=Path("/dev/null")),
            bus=MessageBus(),
            execute_fn=lambda m, c, ci: asyncio.coroutine(lambda: "ok")(),
        )
        nxt = scheduler._compute_next_run(job, _now_ms())
        assert nxt is None

    def test_disabled_job_returns_none(self):
        job = _make_job(enabled=False)
        scheduler = CronScheduler(
            store=CronStore(path=Path("/dev/null")),
            bus=MessageBus(),
            execute_fn=lambda m, c, ci: asyncio.coroutine(lambda: "ok")(),
        )
        nxt = scheduler._compute_next_run(job, _now_ms())
        assert nxt is None


class TestSchedulerExecution:
    """Integration tests with the scheduler loop."""

    @pytest.mark.asyncio
    async def test_every_job_fires(self, tmp_path: Path):
        calls: list[tuple[str, str, str]] = []

        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            calls.append((message, channel, chat_id))
            return f"executed: {message}"

        store = CronStore(path=tmp_path / "jobs.json")
        store.add(_make_job(every_ms=50, message="ping"))
        store.save()

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=10
        )
        await scheduler.start()

        # Wait enough time for at least 2 executions
        await asyncio.sleep(0.2)
        await scheduler.stop()

        assert len(calls) >= 2
        assert calls[0] == ("ping", "telegram", "42")

        # Check outbound messages were published
        count = bus.outbound.qsize()
        assert count >= 2

    @pytest.mark.asyncio
    async def test_at_job_fires_once(self, tmp_path: Path):
        calls: list[str] = []

        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            calls.append(message)
            return "done"

        store = CronStore(path=tmp_path / "jobs.json")
        store.add(_make_job(kind="at", at_ms=_now_ms() + 80, message="once"))
        store.save()

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=10
        )
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()

        assert len(calls) == 1
        assert calls[0] == "once"

    @pytest.mark.asyncio
    async def test_delete_after_run(self, tmp_path: Path):
        calls: list[str] = []

        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            calls.append(message)
            return "done"

        store = CronStore(path=tmp_path / "jobs.json")
        store.add(_make_job(kind="at", at_ms=_now_ms() + 50, delete_after_run=True))
        store.save()

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=10
        )
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()

        assert len(calls) == 1
        # Job should have been removed from store
        assert len(store.jobs) == 0

    @pytest.mark.asyncio
    async def test_disabled_job_does_not_fire(self, tmp_path: Path):
        calls: list[str] = []

        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            calls.append(message)
            return "done"

        store = CronStore(path=tmp_path / "jobs.json")
        store.add(_make_job(enabled=False, every_ms=50))
        store.save()

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=10
        )
        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()

        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_last_run_updated(self, tmp_path: Path):
        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            return "ok"

        store = CronStore(path=tmp_path / "jobs.json")
        store.add(_make_job(every_ms=50))
        store.save()

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=10
        )
        await scheduler.start()
        await asyncio.sleep(0.15)
        await scheduler.stop()

        assert store.jobs[0].last_run_at_ms is not None
        assert store.jobs[0].last_run_at_ms > 0


class TestHotReload:
    @pytest.mark.asyncio
    async def test_hot_reload_picks_up_new_job(self, tmp_path: Path):
        calls: list[str] = []

        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            calls.append(message)
            return "ok"

        store = CronStore(path=tmp_path / "jobs.json")
        store.save()  # Start with empty jobs

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=0.1
        )
        await scheduler.start()

        # No jobs yet
        await asyncio.sleep(0.15)
        assert len(calls) == 0

        # Externally write a new job to the file
        new_job = _make_job(every_ms=50, message="hot-reloaded")
        data = [new_job.to_dict()]
        await asyncio.sleep(0.05)  # Ensure mtime difference
        (tmp_path / "jobs.json").write_text(json.dumps(data))

        # Wait for reload + execution
        await asyncio.sleep(0.4)
        await scheduler.stop()

        assert any(c == "hot-reloaded" for c in calls)

    @pytest.mark.asyncio
    async def test_hot_reload_removes_deleted_job(self, tmp_path: Path):
        calls: list[str] = []

        async def mock_execute(message: str, channel: str, chat_id: str) -> str:
            calls.append(message)
            return "ok"

        store = CronStore(path=tmp_path / "jobs.json")
        store.add(_make_job(every_ms=500, message="will-be-removed"))
        store.save()

        bus = MessageBus()
        scheduler = CronScheduler(
            store=store, bus=bus, execute_fn=mock_execute, reload_interval_s=0.1
        )
        await scheduler.start()

        # Let it fire at least once
        await asyncio.sleep(0.6)
        assert len(calls) >= 1

        # Externally empty the jobs file (between job fires)
        (tmp_path / "jobs.json").write_text('{"jobs": []}')

        # Wait for reload to pick up the change
        await asyncio.sleep(0.3)

        # After reload, snapshot and wait — no new fires should happen
        snapshot = len(calls)
        await asyncio.sleep(0.6)
        await scheduler.stop()

        assert len(calls) == snapshot, "job kept firing after reload removed it"
