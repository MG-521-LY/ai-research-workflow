#!/usr/bin/env python3
"""Atomic, reentrant-aware, fail-safe process mutex lock for workflow directories."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class WorkflowLockError(RuntimeError):
    """Raised when a valid workflow lock is held by another process or cannot be acquired."""
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_pid_alive(pid: int) -> bool:
    """Check if process with PID is alive on Windows/POSIX."""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


class WorkflowLock:
    """
    Atomic process mutex lock (.workflow.lock).
    Supports:
    - Atomic exclusion via exclusive file creation ('x' mode / atomic write)
    - Reentrancy: same process PID + thread can enter nested scopes safely via depth counter
    - Stale lock detection: verified dead PID lock can be reclaimed safely
    - Corrupt lock protection: corrupt lock cannot be silently stolen without explicit instruction
    - Active lock protection: running process lock is never stolen
    """

    def __init__(self, target_dir: str | Path, command: str = "workflow"):
        self.target_dir = Path(target_dir).resolve()
        self.lock_file = self.target_dir / ".workflow.lock"
        self.command = command
        self.is_owner = False
        self.depth = 0

    @property
    def acquired(self) -> bool:
        return self.is_owner and self.depth > 0

    def acquire(self) -> None:
        self.target_dir.mkdir(parents=True, exist_ok=True)
        my_pid = os.getpid()

        # Check existing lock file
        if self.lock_file.is_file():
            try:
                data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                holder_pid = data.get("pid")
                holder_cmd = data.get("command", "unknown")

                # 1. Reentrancy: only if this specific WorkflowLock instance already acquired it
                if self.is_owner and holder_pid == my_pid:
                    self.depth += 1
                    return

                # If another WorkflowLock object (or another process with same PID) tries to acquire:
                if holder_pid and is_pid_alive(holder_pid):
                    raise WorkflowLockError(
                        f"Workflow lock is actively held by PID {holder_pid} (command: '{holder_cmd}', "
                        f"started: {data.get('started_at')}). Cannot run concurrently."
                    )

                # 2. Check if holder process is alive
                if holder_pid and is_pid_alive(holder_pid):
                    raise WorkflowLockError(
                        f"Workflow lock is actively held by PID {holder_pid} (command: '{holder_cmd}', "
                        f"started: {data.get('started_at')}). Cannot run concurrently."
                    )

                # 3. Process is dead: stale lock. Safe to remove.
                print(f"[WARN] Stale lock from dead PID {holder_pid} detected, reclaiming.", file=sys.stderr)
                try:
                    self.lock_file.unlink()
                except OSError:
                    pass

            except (json.JSONDecodeError, OSError) as e:
                # Corrupt lock: Indeterminate state! Block rather than steal.
                raise WorkflowLockError(
                    f"Corrupt lock file detected ({self.lock_file}): {e}. "
                    "Refusing to steal indeterminate lock without manual inspection or --force-unlock."
                )

        # Atomic file creation
        payload = {
            "pid": my_pid,
            "host": socket.gethostname(),
            "command": self.command,
            "started_at": now_iso()
        }
        lock_bytes = json.dumps(payload, indent=2).encode("utf-8")

        # Try exclusive creation ('xb')
        try:
            with open(self.lock_file, "xb") as f:
                f.write(lock_bytes)
            self.is_owner = True
            self.depth = 1
        except FileExistsError:
            # Another process created the lock in the microsecond race window
            raise WorkflowLockError(
                f"Concurrent process acquired lock on {self.target_dir} during race window. Blocked."
            )

    def release(self) -> None:
        if not self.is_owner:
            return

        self.depth -= 1
        if self.depth <= 0:
            self.depth = 0
            if self.lock_file.is_file():
                try:
                    data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                    if data.get("pid") == os.getpid():
                        self.lock_file.unlink()
                except Exception:
                    pass
            self.is_owner = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def inspect_lock(target_dir: str | Path) -> dict:
    lock_file = Path(target_dir).resolve() / ".workflow.lock"
    if not lock_file.is_file():
        return {"locked": False}
    try:
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        pid = data.get("pid")
        alive = is_pid_alive(pid) if pid else False
        return {
            "locked": alive,
            "stale": not alive,
            "holder": data
        }
    except Exception as e:
        return {"locked": True, "corrupt": True, "error": str(e)}
