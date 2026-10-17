import pytest
from pydantic import ValidationError

from rushbase_server.main import load_settings
from rushbase_server.settings import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    for key in ("SECRET_KEY", "DATABASE_URL", "PORT", "HOST", "LOG_LEVEL", "LIBRARY_ROOT"):
        monkeypatch.delenv(f"RUSHBASE_{key}", raising=False)
    monkeypatch.chdir(str(tmp_path))  # no stray .env


def test_secret_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", "postgresql+psycopg://u@h/db")
    with pytest.raises(ValidationError, match="secret_key"):
        Settings()


def test_short_secret_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "short")
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", "postgresql+psycopg://u@h/db")
    with pytest.raises(ValidationError, match="secret_key"):
        Settings()


def test_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", "postgresql+psycopg://u@h/db")
    monkeypatch.setenv("RUSHBASE_PORT", "18080")
    settings = Settings()
    assert settings.port == 18080
    assert settings.host == "127.0.0.1"
    assert "k" * 40 not in repr(settings)


def test_blank_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "")
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", "postgresql+psycopg://u@h/db")
    with pytest.raises(ValidationError, match="secret_key"):
        Settings()


def test_load_settings_exits_cleanly_when_missing(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        load_settings()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "refusing to start" in err
    assert "RUSHBASE_SECRET_KEY" in err


def test_lease_must_outlive_the_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", "postgresql+psycopg://u@h/db")
    monkeypatch.setenv("RUSHBASE_SERVER_LEASE_SECONDS", "20")
    with pytest.raises(ValidationError, match="server_lease_seconds"):
        Settings()
