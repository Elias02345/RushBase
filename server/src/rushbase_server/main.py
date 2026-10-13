"""Server start-up helpers shared by the CLI."""

import sys

import uvicorn
from pydantic import ValidationError

from rushbase_server.app import create_app
from rushbase_server.logging import configure_logging
from rushbase_server.settings import Settings


def load_settings(**overrides: object) -> Settings:
    """Load settings, exiting with a clear message when required values are missing."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]  # other values come from the environment
    except ValidationError as exc:
        problems = "; ".join(
            f"RUSHBASE_{'.'.join(str(p) for p in e['loc']).upper()}: {e['msg']}"
            for e in exc.errors()
        )
        sys.stderr.write(f"[FATAL] invalid configuration, refusing to start: {problems}\n")
        raise SystemExit(2) from exc


def serve(settings: Settings) -> None:
    """Run the API server with uvicorn until it is stopped."""
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )


def main() -> None:
    """Run the API server with settings from the environment."""
    serve(load_settings())


if __name__ == "__main__":
    main()
