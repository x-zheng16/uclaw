from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CronSchedule:
    kind: str  # "at" | "every" | "cron"
    at_ms: int | None = None
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.kind == "at" and self.at_ms is not None:
            d["atMs"] = self.at_ms
        elif self.kind == "every" and self.every_ms is not None:
            d["everyMs"] = self.every_ms
        elif self.kind == "cron":
            if self.expr is not None:
                d["expr"] = self.expr
            if self.tz is not None:
                d["tz"] = self.tz
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CronSchedule:
        return cls(
            kind=d["kind"],
            at_ms=d.get("atMs"),
            every_ms=d.get("everyMs"),
            expr=d.get("expr"),
            tz=d.get("tz"),
        )


@dataclass
class CronJob:
    id: str
    name: str
    schedule: CronSchedule
    message: str
    channel: str
    chat_id: str
    enabled: bool = True
    delete_after_run: bool = False
    last_run_at_ms: int | None = None
    next_run_at_ms: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule.to_dict(),
            "message": self.message,
            "channel": self.channel,
            "chatId": self.chat_id,
            "enabled": self.enabled,
            "deleteAfterRun": self.delete_after_run,
        }
        if self.last_run_at_ms is not None:
            d["lastRunAtMs"] = self.last_run_at_ms
        if self.next_run_at_ms is not None:
            d["nextRunAtMs"] = self.next_run_at_ms
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CronJob:
        return cls(
            id=d["id"],
            name=d["name"],
            schedule=CronSchedule.from_dict(d["schedule"]),
            message=d["message"],
            channel=d["channel"],
            chat_id=d.get("chatId", ""),
            enabled=d.get("enabled", True),
            delete_after_run=d.get("deleteAfterRun", False),
            last_run_at_ms=d.get("lastRunAtMs"),
            next_run_at_ms=d.get("nextRunAtMs"),
        )


@dataclass
class CronStore:
    path: Path
    jobs: list[CronJob] = field(default_factory=list)
    _last_mtime: float = field(default=0.0, repr=False)

    def load(self) -> None:
        if not self.path.exists():
            self.jobs = []
            return
        data = json.loads(self.path.read_text())
        self.jobs = [CronJob.from_dict(j) for j in data]
        self._last_mtime = self.path.stat().st_mtime

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps([j.to_dict() for j in self.jobs], indent=2)
        # Atomic write: write to tmp file in same dir, then rename
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp", prefix=".jobs_")
        try:
            with open(fd, "w") as f:
                f.write(data)
            Path(tmp).replace(self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._last_mtime = self.path.stat().st_mtime

    def add(self, job: CronJob) -> None:
        self.jobs.append(job)

    def remove(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.id != job_id]
        return len(self.jobs) < before

    def has_changed(self) -> bool:
        if not self.path.exists():
            return False
        return self.path.stat().st_mtime != self._last_mtime
