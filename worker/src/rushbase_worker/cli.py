"""The ``rushbase`` command line interface (every command supports ``--json``)."""

import asyncio
import logging
import platform
import socket
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
import typer

from rushbase_core import Stage, parse_stages
from rushbase_worker import get_version
from rushbase_worker.cli_ai import hardware_command, models_app
from rushbase_worker.client import ApiError, AuthError, UnavailableError, WorkerClient
from rushbase_worker.config import WorkerSettings
from rushbase_worker.doctor import run_checks
from rushbase_worker.local import ANALYZE_STAGES, analyze_file
from rushbase_worker.mount import MountError, verify_mount
from rushbase_worker.output import emit_json, envelope
from rushbase_worker.runner import Event, Runner, RunOptions, parse_until
from rushbase_worker.stages import HANDLERS, StageContext, missing_tools, runnable_stages
from rushbase_worker.state import (
    load_api_key,
    load_identity,
    load_last_run,
    save_enrollment,
    save_last_run,
)
from rushbase_worker.tools.toolbox import Toolbox

app = typer.Typer(
    name="rushbase",
    help="Rushbase command line interface.",
    no_args_is_help=True,
    add_completion=False,
)
worker_app = typer.Typer(help="Run the headless worker (`worker run` = `process`).")
app.add_typer(worker_app, name="worker")
app.add_typer(models_app, name="models")
app.command("hardware")(hardware_command)

JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_MOUNT = 3
EXIT_SERVER = 4
EXIT_NOT_ENROLLED = 5


def _fail(command: str, message: str, code: int, as_json: bool, **extra: Any) -> typer.Exit:
    """Report a failure (JSON document on stdout or text on stderr) and return the exit."""
    if as_json:
        emit_json(envelope(command, ok=False, data={"error": message, **extra}))
    else:
        typer.echo(f"error: {message}", err=True)
    return typer.Exit(code)


@app.command()
def version(as_json: JsonOption = False) -> None:
    """Print the installed version."""
    current = get_version()
    if as_json:
        emit_json(envelope("version", ok=True, data={"version": current}))
    else:
        typer.echo(f"rushbase {current}")


@app.command()
def doctor(
    library: Annotated[
        Path | None,
        typer.Option(envvar="RUSHBASE_LIBRARY_ROOT", help="Local mount of the library root."),
    ] = None,
    library_id: Annotated[
        UUID | None,
        typer.Option(envvar="RUSHBASE_LIBRARY_ID", help="Library id the mount must match."),
    ] = None,
    server_url: Annotated[
        str | None,
        typer.Option(envvar="RUSHBASE_SERVER_URL", help="Base URL of the Rushbase server."),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Check external tools, the library mount and server reachability.

    Exits with status 1 when any check fails; unconfigured checks are skipped.
    """
    settings = WorkerSettings()
    checks = run_checks(
        library or settings.resolved_library_root,
        library_id or settings.library_id,
        server_url or settings.server_url,
        settings,
    )
    ok = all(check.status != "error" for check in checks)
    if as_json:
        emit_json(envelope("doctor", ok=ok, data={"checks": [c.to_dict() for c in checks]}))
    else:
        for check in checks:
            typer.echo(f"[{check.status.upper():7}] {check.name}: {check.detail}")
        typer.echo("All checks passed." if ok else "Some checks failed.")
    raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)


@app.command()
def enroll(
    server: Annotated[str, typer.Option(help="Base URL of the Rushbase server.")],
    token: Annotated[str, typer.Option(help="One-time enrollment token from the web UI.")],
    name: Annotated[str, typer.Option(help="Display name of this worker.")],
    as_json: JsonOption = False,
) -> None:
    """Enroll this machine; the API key is stored in the data directory with mode 600."""
    settings = WorkerSettings()
    payload = {
        "token": token,
        "name": name,
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.machine()}".strip(),
        "version": get_version(),
        "capabilities": {"backends": ["cpu"], "stages": [s.value for s in settings.stages]},
    }
    try:
        enrolled = asyncio.run(WorkerClient.enroll(server, payload))
    except AuthError as exc:
        raise _fail(
            "enroll", f"enrollment token rejected: {exc.detail}", EXIT_SERVER, as_json
        ) from exc
    except (ApiError, UnavailableError, httpx.HTTPError) as exc:
        raise _fail("enroll", f"enrollment failed: {exc}", EXIT_SERVER, as_json) from exc
    identity = save_enrollment(settings, enrolled, name, server)
    data = {**identity.model_dump(), "api_key_file": str(settings.resolved_api_key_file)}
    if as_json:
        emit_json(envelope("enroll", ok=True, data=data))
    else:
        typer.echo(f"enrolled as {name} ({identity.worker_id}); key in {data['api_key_file']}")


def _emitter(command: str, as_json: bool) -> Any:
    def emit(event: Event) -> None:
        if as_json:
            failed = event["event"] in {"job_failed", "claim_failed", "report_failed"}
            emit_json(envelope(command, ok=not failed, data=event))
            return
        detail = " ".join(f"{k}={v}" for k, v in event.items() if k != "event")
        typer.echo(f"[{datetime.now().strftime('%H:%M:%S')}] {event['event']} {detail}".rstrip())

    return emit


def _run_process(
    stages: str | None,
    until: str | None,
    exit_when_empty: bool,
    concurrency: int | None,
    as_json: bool,
    command: str = "process",
) -> None:
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(message)s"
    )
    settings = WorkerSettings()
    try:
        wanted = parse_stages(stages.split(",")) if stages else settings.stages
        stop_at = parse_until(until) if until else None
    except ValueError as exc:
        raise _fail(command, str(exc), EXIT_USAGE, as_json) from exc
    settings = settings.with_overrides(concurrency=concurrency)
    identity = load_identity(settings)
    key = load_api_key(settings)
    server_url = settings.server_url or (identity.server_url if identity else None)
    if key is None or server_url is None:
        raise _fail(
            command, "not enrolled: run `rushbase enroll` first", EXIT_NOT_ENROLLED, as_json
        )
    toolbox = Toolbox(settings)
    handlers = runnable_stages(wanted, toolbox)
    if not handlers:
        problems = {s.value: missing_tools(h, toolbox) for s, h in _wanted_handlers(wanted).items()}
        raise _fail(command, f"no runnable stage; missing tools: {problems}", EXIT_FAILED, as_json)
    emit = _emitter(command, as_json)
    started = datetime.now(UTC)

    async def main() -> tuple[int, dict[str, Any]]:
        async with WorkerClient(server_url, key) as client:
            try:
                info = await client.library()
                verify_mount(settings.resolved_library_root, info.library_id, settings.library_id)
            except MountError as exc:
                raise _fail(
                    command, f"library mount check failed: {exc}", EXIT_MOUNT, as_json
                ) from exc
            except (AuthError, ApiError, UnavailableError) as exc:
                raise _fail(command, f"server error: {exc}", EXIT_SERVER, as_json) from exc
            root = settings.resolved_library_root
            assert root is not None
            ctx = StageContext(settings, toolbox, root)
            options = RunOptions(
                concurrency=settings.effective_concurrency,
                heartbeat_seconds=settings.heartbeat_seconds,
                poll_interval_seconds=settings.poll_interval_seconds,
                claim_batch=settings.claim_batch,
                exit_when_empty=exit_when_empty,
                until=stop_at,
            )
            runner = Runner(client, ctx, handlers, options, emit)
            runner.install_signal_handlers()
            try:
                summary = await runner.run()
            except AuthError as exc:
                raise _fail(
                    command, f"server rejected the API key: {exc}", EXIT_SERVER, as_json
                ) from exc
            return EXIT_OK, vars(summary)

    code, summary = asyncio.run(main())
    save_last_run(
        settings,
        {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            **summary,
        },
    )
    raise typer.Exit(code)


def _wanted_handlers(wanted: tuple[Stage, ...]) -> dict[Stage, Any]:
    return {s: h for s, h in HANDLERS.items() if s in wanted}


StagesOption = Annotated[
    str | None, typer.Option(help="Comma separated stages (default: probe,hash,preview,shots).")
]
UntilOption = Annotated[
    str | None, typer.Option(help="Stop at this local time (HH:MM); in-flight jobs are released.")
]
EmptyOption = Annotated[
    bool, typer.Option("--exit-when-empty", help="Exit when the queue has no claimable work.")
]
ConcurrencyOption = Annotated[
    int | None, typer.Option(min=1, help="Parallel jobs (default: CPU count / 2).")
]


@app.command()
def process(
    stages: StagesOption = None,
    until: UntilOption = None,
    exit_when_empty: EmptyOption = False,
    concurrency: ConcurrencyOption = None,
    as_json: JsonOption = False,
) -> None:
    """Claim and process jobs until stopped (SIGINT/SIGTERM release in-flight jobs)."""
    _run_process(stages, until, exit_when_empty, concurrency, as_json)


@worker_app.command("run")
def worker_run(
    stages: StagesOption = None,
    until: UntilOption = None,
    exit_when_empty: EmptyOption = False,
    concurrency: ConcurrencyOption = None,
    as_json: JsonOption = False,
) -> None:
    """Alias of `process` (used by the systemd unit)."""
    _run_process(stages, until, exit_when_empty, concurrency, as_json, command="worker run")


@app.command()
def status(as_json: JsonOption = False) -> None:
    """Show the server backlog (worker key or `RUSHBASE_API_TOKEN`) and the local worker state."""
    settings = WorkerSettings()
    identity = load_identity(settings)
    server_url = settings.server_url or (identity.server_url if identity else None)
    data: dict[str, Any] = {
        "enrolled": load_api_key(settings) is not None,
        "worker": identity.model_dump() if identity else None,
        "last_run": load_last_run(settings),
        "server": None,
        "backlog": None,
    }
    ok = True
    if server_url:

        async def fetch() -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
            key = load_api_key(settings)
            async with WorkerClient(server_url, api_key=key, retries=0, timeout=10.0) as client:
                health = await client.health()
                if not settings.api_token and not key:
                    return (
                        health,
                        None,
                        "enroll this worker or set RUSHBASE_API_TOKEN to read the backlog",
                    )
                try:
                    return health, await client.status(settings.api_token), None
                except ApiError as exc:
                    return health, None, str(exc)

        try:
            health, backlog, note = asyncio.run(fetch())
            data["server"] = {"url": server_url, "reachable": True, "health": health}
            data["backlog"], data["backlog_note"] = backlog, note
        except (ApiError, UnavailableError, httpx.HTTPError) as exc:
            ok = False
            data["server"] = {"url": server_url, "reachable": False, "error": str(exc)}
    if as_json:
        emit_json(envelope("status", ok=ok, data=data))
    else:
        typer.echo(f"enrolled: {data['enrolled']}")
        server = data["server"]
        if server is None:
            typer.echo("server: not configured")
        else:
            typer.echo(
                f"server: {server['url']} ({'reachable' if server['reachable'] else 'UNREACHABLE'})"
            )
        if data["backlog"]:
            for stage, backlog_stage in data["backlog"]["stages"].items():
                typer.echo(f"  {stage}: {backlog_stage['pending_jobs']} pending")
        elif data.get("backlog_note"):
            typer.echo(f"backlog: {data['backlog_note']}")
        if data["last_run"]:
            typer.echo(
                f"last run: {data['last_run']['finished_at']} ({data['last_run']['reason']})"
            )
    raise typer.Exit(EXIT_OK if ok else EXIT_SERVER)


@app.command()
def analyze(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Media file.")],
    stages: Annotated[str, typer.Option(help="Comma separated: probe,preview,shots,hash.")] = (
        ",".join(s.value for s in ANALYZE_STAGES)
    ),
    output_dir: Annotated[
        Path | None, typer.Option(help="Where `.rushbase/derived` is written (default: temp dir).")
    ] = None,
    raw: Annotated[bool, typer.Option("--raw", help="Include the raw tool output.")] = False,
    as_json: JsonOption = False,
) -> None:
    """Probe, preview and analyse one file locally, without a server."""
    settings = WorkerSettings()
    try:
        wanted = parse_stages(stages.split(","))
    except ValueError as exc:
        raise _fail("analyze", f"unknown stage: {exc}", EXIT_USAGE, as_json) from exc
    target = output_dir or Path(tempfile.mkdtemp(prefix="rushbase-analyze-"))
    target.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(analyze_file(settings, file, target, wanted, include_raw=raw))
    ok = all(entry["ok"] for entry in report["stages"].values())
    if as_json:
        emit_json(envelope("analyze", ok=ok, data=report))
    else:
        typer.echo(f"{report['file']} ({report['media_type']}) -> {report['output_dir']}")
        for name, entry in report["stages"].items():
            typer.echo(f"  {name}: {'ok' if entry['ok'] else 'FAILED ' + entry['error']} "
                       f"({entry['duration_ms']} ms)")  # fmt: skip
    raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)
