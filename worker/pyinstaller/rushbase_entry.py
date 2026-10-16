"""Entry point of the frozen `rushbase` sidecar (PyInstaller)."""

import multiprocessing

from rushbase_worker.cli import app

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app()
