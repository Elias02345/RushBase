"""Start, observe and stop a local `rushbase process` run (used by the MCP server)."""

import json
import signal
import subprocess
import sys
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

MAX_EVENTS = 50


def rushbase_command() -> list[str]:
    """Command line prefix that runs this CLI (also inside a frozen sidecar)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-c", "from rushbase_worker.cli import app; app()"]


class ProcessController:
    """At most one `rushbase process --json` child; counters come from its JSON-lines events."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._counters: dict[str, int] = {"done": 0, "failed": 0}
        self._started_at: str | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        """Whether the child is alive."""
        return self._process is not None and self._process.poll() is None

    def start(self, stages: str | None, until: str | None, exit_when_empty: bool) -> dict[str, Any]:
        """Launch a run; raises ``RuntimeError`` when one is already active."""
        with self._lock:
            if self.running:
                raise RuntimeError("processing is already running")
            command = [*rushbase_command(), "process", "--json"]
            if stages:
                command += ["--stages", stages]
            if until:
                command += ["--until", until]
            if exit_when_empty:
                command.append("--exit-when-empty")
            self._events.clear()
            self._counters = {"done": 0, "failed": 0}
            self._started_at = datetime.now(UTC).isoformat()
            self._process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            threading.Thread(target=self._pump, args=(self._process,), daemon=True).start()
            return {"pid": self._process.pid, "started_at": self._started_at}

    def _pump(self, process: "subprocess.Popen[str]") -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                document = json.loads(line)
            except ValueError:
                continue
            event = document.get("data", {})
            if not isinstance(event, dict):
                continue
            with self._lock:
                if event.get("event") == "job_done":
                    self._counters["done"] += 1
                elif event.get("event") == "job_failed":
                    self._counters["failed"] += 1
                self._events.append(event)

    def stop(self, timeout: float = 120.0) -> dict[str, Any]:
        """SIGTERM the run (jobs are released gracefully) and wait for it to exit."""
        process = self._process
        if process is None or process.poll() is not None:
            return {"stopped": False, "reason": "not running"}
        process.send_signal(
            signal.SIGTERM
            if sys.platform != "win32"
            else getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return {"stopped": True, "forced": True}
        return {"stopped": True, "forced": False}

    def status(self) -> dict[str, Any]:
        """Running flag, counters and the most recent events."""
        with self._lock:
            return {
                "running": self.running,
                "started_at": self._started_at,
                "exit_code": self._process.poll() if self._process else None,
                "counters": dict(self._counters),
                "recent_events": list(self._events)[-10:],
            }
