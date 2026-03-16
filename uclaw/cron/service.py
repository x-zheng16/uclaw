from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from uclaw.bus import MessageBus, OutboundMessage
from uclaw.cron.types import CronJob, CronStore

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class CronScheduler:
    def __init__(
        self,
        store: CronStore,
        bus: MessageBus,
        execute_fn: Callable[[str, str, str], Coroutine[Any, Any, str]],
        reload_interval_s: float = 30.0,
    ) -> None:
        self._store = store
        self._bus = bus
        self._execute_fn = execute_fn
        self._reload_interval_s = reload_interval_s
        self._timer_task: asyncio.Task[None] | None = None
        self._reload_task: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        self._stopped = False
        self._store.load()
        self._compute_all_next_runs()
        await self._arm_timer()
        self._reload_task = asyncio.create_task(self._reload_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        if self._reload_task and not self._reload_task.done():
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass

    def _compute_next_run(self, job: CronJob, now_ms: int) -> int | None:
        if not job.enabled:
            return None

        if job.schedule.kind == "at":
            at = job.schedule.at_ms
            if at is not None and at > now_ms:
                return at
            return None

        if job.schedule.kind == "every":
            every = job.schedule.every_ms
            if every is None:
                return None
            if job.last_run_at_ms is not None:
                return job.last_run_at_ms + every
            return now_ms + every

        if job.schedule.kind == "cron":
            from croniter import croniter
            from datetime import datetime, timezone

            if job.schedule.expr is None:
                return None

            if job.schedule.tz:
                import zoneinfo

                tz = zoneinfo.ZoneInfo(job.schedule.tz)
            else:
                tz = timezone.utc

            base = datetime.fromtimestamp(now_ms / 1000, tz=tz)
            cron = croniter(job.schedule.expr, base)
            nxt: datetime = cron.get_next(datetime)
            return int(nxt.timestamp() * 1000)

        return None

    def _compute_all_next_runs(self) -> None:
        now = _now_ms()
        for job in self._store.jobs:
            job.next_run_at_ms = self._compute_next_run(job, now)

    async def _arm_timer(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        self._timer_task = asyncio.create_task(self._timer_loop())

    async def _timer_loop(self) -> None:
        while not self._stopped:
            # Find the next due job
            soonest: CronJob | None = None
            soonest_ms: int | None = None

            for job in self._store.jobs:
                nxt = job.next_run_at_ms
                if nxt is None:
                    continue
                if soonest_ms is None or nxt < soonest_ms:
                    soonest = job
                    soonest_ms = nxt

            if soonest is None or soonest_ms is None:
                # No jobs to run — park until cancelled by _arm_timer or stop
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    return
                continue

            delay_s = max(0, (soonest_ms - _now_ms()) / 1000)
            if delay_s > 0:
                await asyncio.sleep(delay_s)

            if self._stopped:
                break

            # Re-verify the job is still present and due
            job_still_exists = any(j.id == soonest.id for j in self._store.jobs)
            if not job_still_exists:
                continue

            await self._execute_job(soonest)

    async def _execute_job(self, job: CronJob) -> None:
        try:
            result = await self._execute_fn(job.message, job.channel, job.chat_id)
        except Exception:
            logger.exception("cron job %s failed", job.id)
            result = "[error]"

        job.last_run_at_ms = _now_ms()

        # Publish the result to the bus
        await self._bus.publish_outbound(
            OutboundMessage(channel=job.channel, chat_id=job.chat_id, text=result)
        )

        if job.delete_after_run:
            self._store.remove(job.id)
            self._store.save()
        else:
            # Compute next run for repeating jobs
            job.next_run_at_ms = self._compute_next_run(job, _now_ms())
            self._store.save()

    async def _reload_loop(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self._reload_interval_s)
            if self._stopped:
                break
            if self._store.has_changed():
                logger.info("cron store changed on disk, reloading")
                self._store.load()
                self._compute_all_next_runs()
                await self._arm_timer()
