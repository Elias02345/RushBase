#!/usr/bin/env python3
"""Load-test the Rushbase API against the seeded perf database (milestone M7 acceptance).

Starts the real server as a subprocess pinned to four cores (``taskset -c 0-3``, like the
reference 4-core machine) on port 18084, logs in, then drives it with concurrent clients over a
realistic query mix and reports p50/p95/p99 per endpoint. Hybrid search uses the real ONNX
text encoders when ``RUSHBASE_MODELS_DIR`` is set, otherwise only the full-text channel runs.

    RUSHBASE_MODELS_DIR=.toolchain/worker-data/models \\
      uv run python scripts/perf/bench.py \\
        --db-url postgresql+psycopg://rushbase:perf@127.0.0.1:15433/rushbase_perf \\
        --label before --report docs/eval/2026-10-perf.md

The needs auto-matching phase runs in this process (it is a background task of the server, not
an API endpoint) with the same encoder and database.
"""

import argparse
import asyncio
import base64
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import numpy as np
import psycopg
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vocab  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PORT = 18084
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "perf-bench-password-1"
BUDGET_MS = 500.0
Request = tuple[str, str, dict[str, Any]]


@dataclass
class Stats:
    """Latencies (ms) and errors of one endpoint."""

    samples: list[float] = field(default_factory=list)
    errors: int = 0

    def percentile(self, q: float) -> float:
        return float(np.percentile(self.samples, q)) if self.samples else float("nan")


class Workload:
    """Builds requests of the query mix from the vocabulary and the ids of the seeded data."""

    def __init__(self, db_url: str, seed: int) -> None:
        self.py = random.Random(seed)
        plain = make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)
        with psycopg.connect(plain) as conn:
            self.asset_ids = [
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM asset WHERE status = 'filed' ORDER BY random() LIMIT 4000"
                )
            ]
            self.video_ids = [
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM asset WHERE status = 'filed' AND media_type = 'video' "
                    "ORDER BY random() LIMIT 1500"
                )
            ]
            self.total = int(conn.execute("SELECT count(*) FROM asset").fetchone()[0])  # type: ignore[index]
        self.ids_for_cursor = self.asset_ids[:200]

    def words(self) -> str:
        lang = self.py.choice([0, 1])
        pools = [vocab.SUBJECTS, vocab.LOCATIONS, vocab.ACTIONS, vocab.LIGHTS, vocab.MOODS]
        pair = self.py.choice(self.py.choice(pools))[lang]
        return pair.split(" ")[-1] if self.py.random() < 0.5 else pair

    def query(self) -> str:
        kind = self.py.random()
        if kind < 0.35:
            return self.words()
        if kind < 0.75:
            return f"{self.words()} {self.words()}"
        subject = self.py.choice(vocab.SUBJECTS)[1]
        place = self.py.choice(vocab.LOCATIONS)[1]
        light = self.py.choice(vocab.LIGHTS)[1]
        return f"{subject} {place} {light}"

    def filters(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.py.random() < 0.6:
            params["media_type"] = self.py.choice(["video", "video", "photo"])
        if self.py.random() < 0.5:
            params["camera_model"] = self.py.choice(vocab.CAMERAS)[1]
        if self.py.random() < 0.3:
            params["log_profile"] = self.py.choice(vocab.LOG_PROFILES)
        if self.py.random() < 0.3:
            year = self.py.randint(2019, 2026)
            params["date_from"] = date(year, 1, 1).isoformat()
            params["date_to"] = date(year, 12, 31).isoformat()
        if self.py.random() < 0.3:
            params["tag"] = f"location:{vocab_slug(self.py.choice(vocab.LOCATIONS)[1])}"
        if self.py.random() < 0.2:
            params["shot_size"] = self.py.choice(vocab.SHOT_SIZES)
        return params

    def cursor(self, sort: str) -> str:
        """A keyset cursor in the middle of the data (a deep page without walking there)."""
        asset = self.py.choice(self.ids_for_cursor)
        if sort == "date":
            moment = datetime(2018, 1, 1, tzinfo=UTC) + timedelta(days=self.py.randint(100, 2800))
            value: Any = moment.isoformat()
        elif sort == "size":
            value = self.py.randint(10**7, 2 * 10**9)
        else:
            value = (
                f"{self.py.choice('abcdefghijklmnopqrstuvwxyz')}_{self.py.randint(0, 99999):06d}"
            )
        raw = json.dumps([value, asset]).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def build(self, name: str) -> Request:
        """One request of the endpoint class ``name``."""
        py = self.py
        if name == "assets_fts":
            return name, "/api/v1/assets", {"q": self.query(), "limit": 50}
        if name == "search_hybrid":
            return name, "/api/v1/search", {"q": self.query(), "limit": 30}
        if name == "search_hybrid_shots":
            return name, "/api/v1/search", {"q": self.query(), "level": "shot", "limit": 30}
        if name == "search_hybrid_filtered":
            return name, "/api/v1/search", {"q": self.query(), "limit": 30, **self.filters()}
        if name == "assets_filtered":
            return (
                name,
                "/api/v1/assets",
                {"sort": py.choice(["-date", "-size", "filename"]), "limit": 50, **self.filters()},
            )
        if name == "assets_deep_page":
            sort = py.choice(["date", "size", "filename"])
            return (
                name,
                "/api/v1/assets",
                {
                    "sort": f"-{sort}" if sort != "filename" else sort,
                    "cursor": self.cursor(sort),
                    "limit": 50,
                },
            )
        if name == "facets_unfiltered":
            return name, "/api/v1/facets", {}
        if name == "facets_filtered":
            params = self.filters()
            if py.random() < 0.5:
                params["q"] = self.words()
            return name, "/api/v1/facets", params
        if name == "asset_detail":
            return name, f"/api/v1/assets/{py.choice(self.video_ids)}", {}
        if name == "similar":
            return name, f"/api/v1/assets/{py.choice(self.video_ids)}/similar", {"limit": 30}
        if name == "stats":
            return name, "/api/v1/stats", {}
        if name == "capabilities":
            return name, "/api/v1/search/capabilities", {}
        raise KeyError(name)


def vocab_slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


MIX: list[tuple[str, int]] = [
    ("search_hybrid", 22),
    ("search_hybrid_shots", 12),
    ("search_hybrid_filtered", 10),
    ("assets_fts", 10),
    ("assets_filtered", 10),
    ("assets_deep_page", 6),
    ("facets_unfiltered", 5),
    ("facets_filtered", 6),
    ("asset_detail", 12),
    ("similar", 4),
    ("stats", 2),
    ("capabilities", 1),
]


def pin(cores: str) -> None:
    """Pin this process to ``cores`` (a ``taskset`` list such as ``4-7``)."""
    wanted: set[int] = set()
    for part in cores.split(","):
        lo, _, hi = part.partition("-")
        wanted.update(range(int(lo), int(hi or lo) + 1))
    os.sched_setaffinity(0, wanted)


def start_server(
    db_url: str, cores: str, models_dir: str | None, source: str | None
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "RUSHBASE_DATABASE_URL": make_url(db_url)
        .set(drivername="postgresql+psycopg")
        .render_as_string(hide_password=False),
        "RUSHBASE_SECRET_KEY": "b" * 40,
        "RUSHBASE_HOST": "127.0.0.1",
        "RUSHBASE_PORT": str(PORT),
        "RUSHBASE_LOG_LEVEL": "WARNING",
        "RUSHBASE_BACKGROUND_TASKS": "false",
        "RUSHBASE_ENCODER_THREADS": os.environ.get("RUSHBASE_ENCODER_THREADS", "2"),
    }
    env["RUSHBASE_LIBRARY_ROOT"] = tempfile.mkdtemp(
        prefix="rushbase-perf-lib-"
    )  # only for the login
    if source:
        env["PYTHONPATH"] = f"{source}/server/src:{source}/core/src"
    if models_dir:
        env["RUSHBASE_MODELS_DIR"] = models_dir
    prefix = ["taskset", "-c", cores] if shutil.which("taskset") else []
    return subprocess.Popen(
        [*prefix, sys.executable, "-m", "rushbase_server.main"], env=env, cwd=ROOT
    )


async def wait_healthy(client: httpx.AsyncClient) -> None:
    for _ in range(120):
        try:
            if (await client.get("/health")).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("server did not become healthy")


async def login(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/setup", json={"username": "bench", "password": PASSWORD})
    if response.status_code == 409:
        response = await client.post(
            "/api/v1/auth/login", json={"username": "bench", "password": PASSWORD}
        )
    response.raise_for_status()


async def timed(client: httpx.AsyncClient, request: Request, results: dict[str, Stats]) -> None:
    name, path, params = request
    started = time.perf_counter()
    try:
        response = await client.get(path, params=params)
        ok = response.status_code == 200
    except httpx.HTTPError:
        ok = False
    elapsed = (time.perf_counter() - started) * 1000
    stats = results.setdefault(name, Stats())
    if ok:
        stats.samples.append(elapsed)
    else:
        stats.errors += 1


async def sequential(
    client: httpx.AsyncClient, work: Workload, per_endpoint: int
) -> dict[str, Stats]:
    """One client, ``per_endpoint`` requests of every class: the latency without queueing."""
    results: dict[str, Stats] = {}
    for name, _weight in MIX:
        for _ in range(per_endpoint):
            await timed(client, work.build(name), results)
    return results


async def concurrent(
    client: httpx.AsyncClient, work: Workload, clients: int, seconds: float
) -> tuple[dict[str, Stats], float]:
    """``clients`` parallel users picking requests from the weighted mix for ``seconds``."""
    results: dict[str, Stats] = {}
    names = [n for n, w in MIX for _ in range(w)]
    deadline = time.monotonic() + seconds
    started = time.monotonic()

    async def user(seed: int) -> None:
        picker = random.Random(seed)
        while time.monotonic() < deadline:
            await timed(client, work.build(picker.choice(names)), results)

    await asyncio.gather(*(user(i) for i in range(clients)))
    total = sum(len(s.samples) + s.errors for s in results.values())
    return results, total / (time.monotonic() - started)


async def needs_matching(
    db_url: str, models_dir: str | None, samples: int, work: Workload
) -> Stats:
    """Time ``needs.match_asset`` (one hybrid search per open item) in this process."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from rushbase_server.search.encoder import NullEncoder, build_encoder
    from rushbase_server.services import needs
    from rushbase_server.settings import Settings

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="b" * 40,  # type: ignore[arg-type]
        database_url=db_url,  # type: ignore[arg-type]
        models_dir=Path(models_dir) if models_dir else None,
    )
    encoder = build_encoder(settings) if models_dir else NullEncoder()
    engine = create_async_engine(make_url(db_url).set(drivername="postgresql+psycopg"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    stats = Stats()
    for asset_id in work.asset_ids[:samples]:
        started = time.perf_counter()
        async with factory() as session:
            await needs.match_asset(
                session, encoder, UUID(asset_id), settings.needs_match_threshold
            )
            await session.rollback()
        stats.samples.append((time.perf_counter() - started) * 1000)
    await engine.dispose()
    return stats


def table(results: dict[str, Stats], order: list[str]) -> list[str]:
    lines = [
        "| Endpoint | n | err | p50 ms | p95 ms | p99 ms | max ms | p95 < 500 |",
        "|---|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for name in order:
        s = results.get(name)
        if s is None or not s.samples:
            continue
        p95 = s.percentile(95)
        lines.append(
            f"| `{name}` | {len(s.samples)} | {s.errors} | {s.percentile(50):.0f} | {p95:.0f} | "
            f"{s.percentile(99):.0f} | {max(s.samples):.0f} | {'yes' if p95 < BUDGET_MS else '**NO**'} |"
        )
    return lines


def render(
    label: str,
    meta: dict[str, Any],
    seq: dict[str, Stats],
    conc: dict[str, Stats],
    rps: float,
    needs_stats: Stats | None,
) -> str:
    order = [n for n, _w in MIX]
    lines = [f"## Run: {label}", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += ["", "### One client (latency without queueing)", ""] + table(seq, order)
    lines += [
        "",
        f"### {meta['clients']} concurrent clients, mixed workload ({rps:.1f} requests/s)",
        "",
    ] + table(conc, order)
    if needs_stats is not None:
        extra = {"needs_match": needs_stats}
        lines += [
            "",
            "### Needs auto-matching (`match_asset` for one filed asset, all open items)",
            "",
        ] + table(extra, ["needs_match"])
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> None:
    work = Workload(args.db_url, args.seed)
    cores = args.server_cores
    models_dir = os.environ.get("RUSHBASE_MODELS_DIR") or None
    server = (
        None if args.no_server else start_server(args.db_url, cores, models_dir, args.server_src)
    )
    pin(args.client_cores)
    try:
        limits = httpx.Limits(max_connections=args.clients * 2)
        async with httpx.AsyncClient(base_url=BASE, timeout=120, limits=limits) as client:
            await wait_healthy(client)
            await login(client)
            caps = (await client.get("/api/v1/search/capabilities")).json()
            first = time.perf_counter()
            await client.get("/api/v1/search", params={"q": "harbour"})
            first_ms = (time.perf_counter() - first) * 1000
            for _ in range(args.warmup):
                await timed(client, work.build(random.choice(MIX)[0]), {})
            seq = await sequential(client, work, args.per_endpoint)
            conc, rps = await concurrent(client, work, args.clients, args.seconds)
        needs_stats = (
            await needs_matching(args.db_url, models_dir, args.needs_samples, work)
            if args.needs_samples
            else None
        )
    finally:
        if server is not None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
    meta = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "assets": work.total,
        "server": f"rushbase-server pinned to cores {cores} (taskset)",
        "encoders": "real ONNX text towers" if models_dir else "none (full-text channel only)",
        "text_semantic": caps["text_semantic"]["available"],
        "image_semantic": caps["image_semantic"]["available"],
        "first search after start": f"{first_ms:.0f} ms",
        "clients": args.clients,
        "duration": f"{args.seconds:.0f} s",
    }
    text = render(args.label, meta, seq, conc, rps, needs_stats)
    print(text)
    if args.report:
        path = Path(args.report)
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(previous + ("\n" if previous else "") + text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    n: {
                        "p50": s.percentile(50),
                        "p95": s.percentile(95),
                        "p99": s.percentile(99),
                        "n": len(s.samples),
                        "errors": s.errors,
                    }
                    for n, s in conc.items()
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    worst = max(
        (s.percentile(95) for s in [*seq.values(), *conc.values()] if s.samples), default=0.0
    )
    print(f"worst p95: {worst:.0f} ms (budget {BUDGET_MS:.0f} ms)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db-url", required=True, help="SQLAlchemy URL of the seeded database")
    parser.add_argument("--label", default="run")
    parser.add_argument("--clients", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument(
        "--per-endpoint", type=int, default=40, help="requests per endpoint in the one-client pass"
    )
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument("--needs-samples", type=int, default=30)
    parser.add_argument("--server-cores", default="0-3")
    parser.add_argument("--client-cores", default="4-7")
    parser.add_argument(
        "--no-server", action="store_true", help="use the server already running on port 18084"
    )
    parser.add_argument("--report", help="markdown file to append the run to")
    parser.add_argument("--json", help="write per-endpoint percentiles of the concurrent run")
    parser.add_argument(
        "--server-src", help="source tree (with server/src and core/src) to run instead of this one"
    )
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
