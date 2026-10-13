"""The ``rushbase`` command line interface."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from rushbase_worker import get_version
from rushbase_worker.doctor import run_checks
from rushbase_worker.output import emit_json, envelope

app = typer.Typer(
    name="rushbase",
    help="Rushbase command line interface.",
    no_args_is_help=True,
    add_completion=False,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]

EXIT_OK = 0
EXIT_FAILED = 1


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
    checks = run_checks(library, library_id, server_url)
    ok = all(check.status != "error" for check in checks)
    if as_json:
        emit_json(envelope("doctor", ok=ok, data={"checks": [c.to_dict() for c in checks]}))
    else:
        for check in checks:
            typer.echo(f"[{check.status.upper():7}] {check.name}: {check.detail}")
        typer.echo("All checks passed." if ok else "Some checks failed.")
    raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)
