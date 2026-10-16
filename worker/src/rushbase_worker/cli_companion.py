"""CLI commands of the companion: login, search, asset, select, paths, resolve, export, mcp."""

import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from rushbase_worker.companion import service
from rushbase_worker.companion.api import (
    UserApiError,
    login_and_create_token,
    save_user_token,
)
from rushbase_worker.companion.clips import local_path
from rushbase_worker.companion.export_csv import DEFAULT_COLUMNS
from rushbase_worker.companion.resolve import client as resolve_client
from rushbase_worker.companion.resolve.selftest import selftest as run_selftest
from rushbase_worker.companion.selection import Basket, load_basket
from rushbase_worker.config import WorkerSettings
from rushbase_worker.output import emit_json, envelope

JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
EXIT_FAILED = 1
EXIT_SERVER = 4

select_app = typer.Typer(help="The local selection basket.", no_args_is_help=True)
asset_app = typer.Typer(help="Asset details.", no_args_is_help=True)
paths_app = typer.Typer(help="Map library paths to this machine.", no_args_is_help=True)
resolve_app = typer.Typer(help="DaVinci Resolve Studio integration.", no_args_is_help=True)
export_app = typer.Typer(help="Export the selection without scripting.", no_args_is_help=True)
mcp_app = typer.Typer(help="Local MCP server for agents.", no_args_is_help=True)


def _fail(command: str, message: str, as_json: bool, code: int = EXIT_FAILED) -> typer.Exit:
    if as_json:
        emit_json(envelope(command, ok=False, data={"error": message}))
    else:
        typer.echo(f"error: {message}", err=True)
    return typer.Exit(code)


def _guard(command: str, as_json: bool, action: Any) -> Any:
    """Run ``action`` and turn companion errors into a failure document."""
    try:
        return action()
    except service.CompanionError as exc:
        raise _fail(command, str(exc), as_json) from exc
    except resolve_client.ResolveError as exc:
        raise _fail(command, str(exc), as_json) from exc


def _basket_data(basket: Basket) -> dict[str, Any]:
    return {
        "count": len(basket.items),
        "items": [
            {"asset_id": c.asset_id, "filename": c.filename, "selected_shots": c.selected,
             "shots": len(c.shots), "duration_ms": c.duration_ms}
            for c in basket.items
        ],
    }  # fmt: skip


def login(
    server: Annotated[str, typer.Option(help="Base URL of the Rushbase server.")],
    username: Annotated[str, typer.Option(help="Rushbase user name.")],
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="Read the password from stdin.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Sign in once and store a personal API token (used by search, asset and select)."""
    settings = WorkerSettings()
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = typer.prompt("Password", hide_input=True)
    try:
        token = login_and_create_token(server, username, password)
    except UserApiError as exc:
        raise _fail("login", str(exc), as_json, EXIT_SERVER) from exc
    path = save_user_token(settings, token)
    data = {"server_url": server, "username": username, "token_file": str(path)}
    if as_json:
        emit_json(envelope("login", ok=True, data=data))
    else:
        typer.echo(f"signed in as {username}; token stored in {path}")


def search(
    query: Annotated[str, typer.Argument(help="Free-text query.")],
    filter_: Annotated[
        list[str] | None,
        typer.Option("--filter", help="key=value (repeatable), e.g. tag=location:harbour."),
    ] = None,
    level: Annotated[str, typer.Option(help="`asset` or `shot`.")] = "asset",
    limit: Annotated[int, typer.Option(min=1, max=100)] = 30,
    offset: Annotated[int, typer.Option(min=0)] = 0,
    as_json: JsonOption = False,
) -> None:
    """Hybrid search on the server."""
    result = _guard(
        "search", as_json,
        lambda: service.search(WorkerSettings(), query, filter_ or [], level, limit, offset),
    )  # fmt: skip
    if as_json:
        emit_json(envelope("search", ok=True, data=result))
        return
    for hit in result["items"]:
        asset = hit["asset"]
        shot = hit.get("shot")
        where = f" shot {shot['idx']} @{shot['start_ms']} ms" if shot else ""
        typer.echo(f"{asset['id']}  {asset['filename']}{where}  {asset.get('summary') or ''}")
    typer.echo(f"{result['candidates']} candidates")


def browse(
    filter_: Annotated[
        list[str] | None, typer.Option("--filter", help="key=value (repeatable).")
    ] = None,
    sort: Annotated[str | None, typer.Option(help="e.g. -date, filename, -size.")] = None,
    cursor: Annotated[str | None, typer.Option(help="`next_cursor` of the previous page.")] = None,
    limit: Annotated[int, typer.Option(min=1, max=200)] = 60,
    as_json: JsonOption = False,
) -> None:
    """List assets without a query (the library grid)."""
    page = _guard(
        "browse", as_json, lambda: service.browse(WorkerSettings(), filter_, sort, cursor, limit)
    )
    if as_json:
        emit_json(envelope("browse", ok=True, data=page))
        return
    for item in page["items"]:
        typer.echo(f"{item['id']}  {item['filename']}  {item.get('summary') or ''}")


def filters_command(as_json: JsonOption = False) -> None:
    """The available filter dimensions (facets) with values and counts."""
    data = _guard("filters", as_json, lambda: service.list_filters(WorkerSettings()))
    if as_json:
        emit_json(envelope("filters", ok=True, data=data))
        return
    for facet in data["facets"]:
        typer.echo(f"{facet['key']} ({facet['kind']})")


@asset_app.command("get")
def asset_get(asset_id: str, as_json: JsonOption = False) -> None:
    """Asset detail with the path on this machine."""
    detail = _guard("asset get", as_json, lambda: service.get_asset(WorkerSettings(), asset_id))
    if as_json:
        emit_json(envelope("asset get", ok=True, data=detail))
    else:
        typer.echo(f"{detail['filename']} ({detail['media_type']}, {len(detail['shots'])} shots)")
        typer.echo(f"local: {detail['local_path']} (exists: {detail['local_exists']})")


def _shots_option(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        return [int(part) for part in value.split(",")]
    except ValueError as exc:
        raise typer.BadParameter("expected comma separated shot indexes") from exc


def _print_basket(command: str, basket: Basket, as_json: bool) -> None:
    if as_json:
        emit_json(envelope(command, ok=True, data=_basket_data(basket)))
    else:
        typer.echo(f"{len(basket.items)} asset(s) selected")
        for item in basket.items:
            typer.echo(f"  {item.asset_id}  {item.filename}  shots={item.selected or 'all'}")


@select_app.command("add")
def select_add(
    asset_id: str,
    shots: Annotated[str | None, typer.Option(help="Comma separated shot indexes.")] = None,
    as_json: JsonOption = False,
) -> None:
    """Add an asset (or some shots) to the basket."""
    basket = _guard("select add", as_json,
                    lambda: service.select_add(WorkerSettings(), asset_id, _shots_option(shots)))  # fmt: skip
    _print_basket("select add", basket, as_json)


@select_app.command("remove")
def select_remove(
    asset_id: str,
    shots: Annotated[str | None, typer.Option(help="Only remove these shot indexes.")] = None,
    as_json: JsonOption = False,
) -> None:
    """Remove an asset (or some shots) from the basket."""
    basket = _guard("select remove", as_json,
                    lambda: service.select_remove(WorkerSettings(), asset_id, _shots_option(shots)))  # fmt: skip
    _print_basket("select remove", basket, as_json)


@select_app.command("list")
def select_list(as_json: JsonOption = False) -> None:
    """Show the basket."""
    _print_basket("select list", load_basket(WorkerSettings()), as_json)


@select_app.command("clear")
def select_clear(as_json: JsonOption = False) -> None:
    """Empty the basket."""
    _print_basket("select clear", service.select_clear(WorkerSettings()), as_json)


@paths_app.command("resolve")
def paths_resolve(asset_id: str, as_json: JsonOption = False) -> None:
    """Per-OS paths of an asset's files and where they are on this machine."""
    data = _guard(
        "paths resolve", as_json, lambda: service.resolve_paths(WorkerSettings(), asset_id)
    )
    if as_json:
        emit_json(envelope("paths resolve", ok=True, data=data))
    else:
        for entry in data["files"]:
            typer.echo(f"{entry['role']:10} {entry['local_path']}  (exists: {entry['exists']})")


@resolve_app.command("status")
def resolve_status(as_json: JsonOption = False) -> None:
    """Detect DaVinci Resolve Studio: installed, edition, version, reachable, open project."""
    data = resolve_client.resolve_status(WorkerSettings())
    ok = bool(data["reachable"] and data["studio"])
    if as_json:
        emit_json(envelope("resolve status", ok=ok, data=data))
    else:
        typer.echo(f"installed: {data['installed']}  reachable: {data['reachable']}  "
                   f"studio: {data['studio']}  version: {data['version']}  project: {data['project']}")  # fmt: skip
        if data["error"]:
            typer.echo(f"note: {data['error']}")
    raise typer.Exit(0 if ok else EXIT_FAILED)


@resolve_app.command("send")
def resolve_send(
    bin_: Annotated[str, typer.Option("--bin", help="Bin (media pool folder) name.")] = "Rushbase",
    markers: Annotated[
        bool, typer.Option("--markers", help="Add markers at shot boundaries.")
    ] = False,
    metadata: Annotated[bool, typer.Option("--metadata", help="Set clip metadata.")] = False,
    project: Annotated[str | None, typer.Option(help="Abort unless this project is open.")] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm writing into the open project.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Import the selection into the open Resolve project (idempotent)."""
    if not yes:
        raise _fail(
            "resolve send", "this writes into the open Resolve project: pass --yes", as_json, 2
        )
    settings = WorkerSettings()
    result = _guard(
        "resolve send", as_json,
        lambda: resolve_client.push_clips(
            settings, service.basket_clips(settings), lambda c: local_path(settings, c),
            bin_name=bin_, markers=markers, metadata=metadata, project=project,
        ),
    )  # fmt: skip
    ok = not result["errors"]
    if as_json:
        emit_json(envelope("resolve send", ok=ok, data=result))
    else:
        typer.echo(f"imported {result['imported']}, existing {result['existing']}, "
                   f"markers {result['markers_added']}, metadata {result['metadata_set']}")  # fmt: skip
        for error in result["errors"]:
            typer.echo(f"error: {error}", err=True)
    raise typer.Exit(0 if ok else EXIT_FAILED)


@resolve_app.command("selftest")
def resolve_selftest(
    project: Annotated[
        str, typer.Option(help="Throwaway project; must start with rushbase-test.")
    ] = "rushbase-test",
    yes: Annotated[
        bool, typer.Option("--yes", help="I allow switching Resolve to the test project.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Round-trip check in a dedicated throwaway project (never touches other projects)."""
    if not yes:
        raise _fail(
            "resolve selftest",
            "this opens the project and switches the Resolve session: pass --yes",
            as_json,
            2,
        )
    settings = WorkerSettings()
    workdir = settings.data_dir / "resolve-selftest"
    result = _guard("resolve selftest", as_json, lambda: run_selftest(settings, project, workdir))
    if as_json:
        emit_json(envelope("resolve selftest", ok=result["ok"], data=result))
    else:
        for name, passed in result["checks"].items():
            typer.echo(f"[{'OK' if passed else 'FAIL'}] {name}")
    raise typer.Exit(0 if result["ok"] else EXIT_FAILED)


def _export(
    kind: str, out: Path, columns: str | None, assets: list[str] | None, title: str, as_json: bool
) -> None:
    settings = WorkerSettings()
    cols = tuple(c.strip() for c in columns.split(",")) if columns else None
    text, warnings = _guard(f"export {kind}", as_json,
                            lambda: service.export_text(settings, kind, cols, assets, title))  # fmt: skip
    service.write_export(out, text, kind)
    data = {"format": kind, "file": str(out), "bytes": out.stat().st_size, "warnings": warnings}
    if as_json:
        emit_json(envelope(f"export {kind}", ok=True, data=data))
    else:
        typer.echo(f"wrote {out}")
        for warning in warnings:
            typer.echo(f"warning: {warning}", err=True)


OutOption = Annotated[Path, typer.Option("--out", help="Output file.")]
AssetsOption = Annotated[
    list[str] | None, typer.Option("--asset", help="Only these assets (repeatable).")
]
TitleOption = Annotated[str, typer.Option(help="Timeline / list title.")]


@export_app.command("fcpxml")
def export_fcpxml_command(
    out: OutOption,
    asset: AssetsOption = None,
    title: TitleOption = "Rushbase selection",
    as_json: JsonOption = False,
) -> None:
    """FCPXML 1.10 with the selected shots as clips on one timeline."""
    _export("fcpxml", out, None, asset, title, as_json)


@export_app.command("edl")
def export_edl_command(
    out: OutOption,
    asset: AssetsOption = None,
    title: TitleOption = "Rushbase selection",
    as_json: JsonOption = False,
) -> None:
    """CMX 3600 EDL cut list."""
    _export("edl", out, None, asset, title, as_json)


@export_app.command("csv")
def export_csv_command(
    out: OutOption,
    columns: Annotated[
        str | None,
        typer.Option(help=f"Comma separated header names (default: {','.join(DEFAULT_COLUMNS)})."),
    ] = None,
    asset: AssetsOption = None,
    as_json: JsonOption = False,
) -> None:
    """Resolve metadata CSV (Import Metadata from File)."""
    _export("csv", out, columns, asset, "", as_json)


@mcp_app.command("serve")
def mcp_serve(
    stdio: Annotated[
        bool, typer.Option("--stdio", help="Serve on stdin/stdout (default).")
    ] = False,
    http: Annotated[
        str | None,
        typer.Option(help="Serve on HOST:PORT (streamable HTTP, bearer token required)."),
    ] = None,
    token_file: Annotated[
        Path | None, typer.Option(help="File with the bearer token for --http.")
    ] = None,
) -> None:
    """Run the local MCP server (tools mirror the CLI; nothing deletes or moves files)."""
    from rushbase_worker.companion.mcp_server import serve

    if http and stdio:
        raise typer.BadParameter("choose --stdio or --http, not both")
    if http and token_file is None:
        raise typer.BadParameter("--http requires --token-file")
    serve(WorkerSettings(), http=http, token_file=token_file)


def overview(
    no_mount_check: Annotated[
        bool, typer.Option("--no-mount-check", help="Skip the server round trip for the mount.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Backlog, estimate, hardware, models and guided issues in one document (Processor home)."""
    data = service.overview(WorkerSettings(), probe_mount=not no_mount_check)
    if as_json:
        emit_json(envelope("overview", ok=True, data=data))
        return
    totals, hw = data["totals"], data["hardware"]
    typer.echo(f"backlog: {totals['files']} files, {totals['hours']} h, {totals['jobs']} jobs")
    typer.echo(f"hardware: {hw['gpu'] or hw['cpu']} / {hw['backend']} / {hw['profile']}")
    if data["estimate"]:
        typer.echo(f"estimated: {data['estimate']['total_seconds'] // 60} min")
    for issue in data["issues"]:
        typer.echo(f"issue [{issue['code']}]: {issue['message']}")
