"""The stable JSON envelope shared by every ``--json`` command (see docs/cli-json.md)."""

import json
from typing import Any

import typer

JSON_SCHEMA_VERSION = 1


def envelope(command: str, *, ok: bool, data: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON document printed by ``rushbase <command> --json``."""
    return {"schema_version": JSON_SCHEMA_VERSION, "command": command, "ok": ok, "data": data}


def emit_json(document: dict[str, Any]) -> None:
    """Print ``document`` as a single JSON line on stdout."""
    typer.echo(json.dumps(document, ensure_ascii=False, sort_keys=True))
