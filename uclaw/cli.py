"""Simple process management for uclaw daemon."""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

LOCK_FILE = Path.home() / ".uclaw" / "bridge.lock"
PID_FILE = Path.home() / ".uclaw" / "bridge.pid"


def _acquire_lock() -> int | None:
    """Try to acquire the lock file. Returns fd on success, None if already held."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # check if alive
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def cmd_start() -> None:
    fd = _acquire_lock()
    if fd is None:
        pid = _read_pid()
        print(f"Already running (pid {pid or '?'})")
        return
    # Release lock here — the daemon subprocess will acquire it itself
    os.close(fd)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uclaw"],
        stdout=open(Path.home() / ".uclaw" / "logs" / "bridge.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _write_pid(proc.pid)
    print(f"Started (pid {proc.pid})")
    print(f"Logs: tail -f ~/.uclaw/logs/bridge.log")


def cmd_stop() -> None:
    pid = _read_pid()
    if not pid:
        print("Not running")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):  # wait up to 3s
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            PID_FILE.unlink(missing_ok=True)
            print("Stopped")
            return
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    print("Force killed")


def cmd_restart() -> None:
    cmd_stop()
    time.sleep(0.5)
    cmd_start()


def cmd_status() -> None:
    fd = _acquire_lock()
    if fd is not None:
        os.close(fd)
        print("Not running")
    else:
        pid = _read_pid()
        print(f"Running (pid {pid or '?'})")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uclaw {start|stop|restart|status|run}")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "restart":
        cmd_restart()
    elif cmd == "status":
        cmd_status()
    elif cmd == "run":
        # Run in foreground (for systemd or direct use)
        import asyncio
        from .__main__ import main as async_main
        asyncio.run(async_main())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
