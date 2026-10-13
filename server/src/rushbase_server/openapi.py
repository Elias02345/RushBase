"""Dump the OpenAPI schema (used by ``pnpm gen:api``): ``python -m rushbase_server.openapi``."""

import json
import sys

from pydantic import SecretStr

from rushbase_server.app import create_app
from rushbase_server.settings import Settings


def main() -> None:
    """Print the OpenAPI document to stdout.

    The settings below only satisfy validation; the app is never served, so no
    lifespan runs and nothing connects to a database.
    """
    settings = Settings(
        secret_key=SecretStr("x" * 32),
        database_url=SecretStr("postgresql+psycopg://openapi-export/none"),
    )
    json.dump(create_app(settings).openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
