#!/usr/bin/env python3
"""A minimal fake worker for protocol tests (used by scripts/kill-test.sh).

Modes:
  hang    claim one job, then heartbeat forever without finishing (to be killed with SIGKILL)
  finish  claim jobs and complete them, until --expect-job-id has been completed

Every claim and completion is appended as a JSON line to --state-file.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

CAPABILITIES = {"backends": ["cpu"], "stages": ["probe"]}


def log(state_file: Path, **event: Any) -> None:
    """Append one JSON event line to the state file."""
    with state_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def enroll(client: httpx.Client, token: str, name: str) -> dict[str, Any]:
    """Exchange the enrollment token for an API key."""
    response = client.post(
        "/api/v1/worker/enroll",
        json={"token": token, "name": name, "capabilities": CAPABILITIES},
    )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


def run(args: argparse.Namespace) -> int:
    """Run the worker loop for the selected mode."""
    state = Path(args.state_file)
    with httpx.Client(base_url=args.server, timeout=10) as client:
        enrolled = enroll(client, args.token, args.name)
        client.headers["Authorization"] = f"Bearer {enrolled['api_key']}"
        heartbeat = float(enrolled["heartbeat_seconds"])
        log(state, event="enrolled", worker_id=enrolled["worker_id"], pid=_pid())
        held: list[dict[str, Any]] = []
        while True:
            if not held:
                claim = client.post("/api/v1/worker/claim", json={"max_jobs": 1}).json()
                held = claim["jobs"]
                for job in held:
                    log(state, event="claimed", job_id=job["job_id"], attempt=job["attempt"])
                if not held:
                    time.sleep(0.5)
                    continue
            if args.mode == "hang":
                for job in held:
                    client.post(f"/api/v1/worker/jobs/{job['job_id']}/heartbeat")
                time.sleep(min(heartbeat, 1.0))
                continue
            for job in held:
                body = {
                    "ok": True,
                    "output": {"fake": True},
                    "model_id": "fake",
                    "model_version": "1",
                }
                done = client.post(f"/api/v1/worker/jobs/{job['job_id']}/result", json=body).json()
                log(state, event="completed", job_id=job["job_id"], **done)
                if job["job_id"] == args.expect_job_id:
                    return 0
            held = []


def _pid() -> int:
    import os

    return os.getpid()


def main() -> int:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--token", required=True, help="one-time enrollment token")
    parser.add_argument("--name", default="fake-worker")
    parser.add_argument("--mode", choices=["hang", "finish"], required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--expect-job-id", default="")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
