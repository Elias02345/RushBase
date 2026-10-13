"""``rushbase-server`` command line: serve (default), migrate and backup management."""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from rushbase_core import LibraryError
from rushbase_server.db import create_engine, create_session_factory
from rushbase_server.logging import configure_logging
from rushbase_server.main import load_settings, serve
from rushbase_server.services import backup
from rushbase_server.settings import Settings

app = typer.Typer(
    help="Rushbase coordinator server. Without a command the server starts.",
    no_args_is_help=False,
    add_completion=False,
)
backup_app = typer.Typer(help="Database backups on the share.", add_completion=False)
app.add_typer(backup_app, name="backup")

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Take over the library even if another server's lease is still valid.",
        ),
    ] = False,
) -> None:
    """Start the server when no sub command is given."""
    if ctx.invoked_subcommand is not None:
        return
    overrides = {"force_takeover": True} if force_takeover else {}
    try:
        serve(load_settings(**overrides))
    except LibraryError as exc:
        sys.stderr.write(f"[FATAL] {exc}\n")
        raise typer.Exit(3) from exc


def _migrate_config() -> Config:
    """Alembic configuration; ``migrations/env.py`` reads the database URL from the settings."""
    return Config(str(ALEMBIC_INI))


@app.command()
def migrate() -> None:
    """Apply database migrations (``alembic upgrade head``)."""
    load_settings()  # validates the configuration before touching the database
    command.upgrade(_migrate_config(), "head")
    typer.echo("[OK] database is up to date")


def _backup_dir(settings: Settings) -> Path:
    if settings.library_root is None:
        typer.echo("[ERROR] RUSHBASE_LIBRARY_ROOT is not set", err=True)
        raise typer.Exit(2)
    return backup.backup_dir(settings.library_root)


@backup_app.command("now")
def backup_now() -> None:
    """Create a backup immediately."""
    settings = load_settings()
    configure_logging(settings.log_level)
    _backup_dir(settings)  # exits when no library root is configured

    async def run() -> backup.BackupInfo:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as session:
                return await backup.run_backup(
                    session,
                    backup.PgTools.from_settings(settings),
                    make_url(settings.database_url.get_secret_value()),
                    settings.library_root,  # type: ignore[arg-type]  # checked by _backup_dir
                    datetime.now(UTC),
                    keep_interval=settings.backup_keep_interval,
                    keep_daily=settings.backup_keep_daily,
                )
        finally:
            await engine.dispose()

    try:
        info = asyncio.run(run())
    except backup.BackupError as exc:
        typer.echo(f"[ERROR] {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"[OK] {info.path} ({info.size} bytes, {info.kind})")


@backup_app.command("list")
def backup_list() -> None:
    """List backups on the share, newest first."""
    settings = load_settings()
    for item in backup.list_backups(_backup_dir(settings)):
        typer.echo(f"{item.name}\t{item.size}\t{item.created_at.isoformat()}\t{item.kind}")


@backup_app.command("restore")
def backup_restore(
    file: Annotated[
        Path, typer.Argument(help="Backup file (name inside the backup directory ok).")
    ],
) -> None:
    """Restore a backup into an empty database, then apply newer migrations."""
    settings = load_settings()
    configure_logging(settings.log_level)
    source = file if file.exists() else _backup_dir(settings) / file.name

    async def run() -> None:
        engine = create_engine(settings)
        try:
            await backup.restore_backup(
                engine,
                backup.PgTools.from_settings(settings),
                make_url(settings.database_url.get_secret_value()),
                source,
            )
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    except backup.BackupError as exc:
        typer.echo(f"[ERROR] {exc}", err=True)
        raise typer.Exit(1) from exc
    command.upgrade(_migrate_config(), "head")
    typer.echo(f"[OK] restored {source.name}; migrations applied")
