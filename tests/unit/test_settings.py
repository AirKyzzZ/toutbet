import secrets
import subprocess
import warnings
from pathlib import Path
from typing import Any

import pytest

from support import random_seed
from toutbet import config
from toutbet.config import ConfigurationError, load_settings, load_worker_settings

SECRET_NAMES = ("JWT_SECRET", "RECEIPT_SIGNING_KEY", "PSP_API_KEY", "PSP_WEBHOOK_SECRET")


def _valid() -> dict[str, Any]:
    return {
        "_env_file": None,
        "jwt_secret": secrets.token_urlsafe(48),
        "receipt_signing_key": random_seed(),
        "psp_base_url": "https://psp.test",
        "psp_api_key": secrets.token_urlsafe(48),
        "psp_webhook_secret": secrets.token_urlsafe(48),
        "public_base_url": "https://toutbet.test",
    }


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*SECRET_NAMES, "ENV", "PSP_BASE_URL", "PUBLIC_BASE_URL", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


def test_settings_refuse_missing_or_weak_secrets() -> None:
    assert load_settings(**_valid()).env == "prod"
    cases = {
        "JWT_SECRET": {"jwt_secret": None},
        "PSP_WEBHOOK_SECRET": {"psp_webhook_secret": "short-secret"},
        "PSP_API_KEY": {"psp_api_key": ""},
        "RECEIPT_SIGNING_KEY": {"receipt_signing_key": "bm90LTMyLWJ5dGVz"},
    }

    for name, change in cases.items():
        values = _valid()
        for key, value in change.items():
            if value is None:
                values.pop(key)
            else:
                values[key] = value
        with pytest.raises(ConfigurationError) as error:
            load_settings(**values)
        assert name in str(error.value)


def test_settings_error_does_not_print_secret_value() -> None:
    weak = "weak-but-very-recognisable"
    values = {**_valid(), "jwt_secret": weak, "psp_webhook_secret": weak + "-2"}

    with pytest.raises(ConfigurationError) as error:
        load_settings(**values)

    message = str(error.value)
    assert "JWT_SECRET" in message
    assert "PSP_WEBHOOK_SECRET" in message
    assert weak not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_prod_refuses_plain_http_psp_and_public_urls() -> None:
    for name in ("PSP_BASE_URL", "PUBLIC_BASE_URL"):
        values = {**_valid(), name.lower(): "http://plain-host.test"}

        with pytest.raises(ConfigurationError) as error:
            load_settings(**values, env="prod")

        assert f"{name} must use https when ENV=prod" in str(error.value)
        assert "plain-host.test" not in str(error.value)
        assert load_settings(**values, env="dev").env == "dev"

    with pytest.raises(ConfigurationError) as worker_error:
        load_worker_settings(
            _env_file=None,
            env="prod",
            psp_base_url="http://plain-host.test",
            psp_api_key=secrets.token_urlsafe(48),
        )
    assert "PSP_BASE_URL must use https when ENV=prod" in str(worker_error.value)


def test_settings_reject_wildcard_cors_origin() -> None:
    with pytest.raises(ConfigurationError) as error:
        load_settings(**{**_valid(), "cors_origins": "*"})

    assert "CORS_ORIGINS" in str(error.value)


def test_secrets_are_masked_in_repr() -> None:
    values = _valid()

    settings = load_settings(**values)

    assert values["jwt_secret"] not in repr(settings)
    assert values["psp_api_key"] not in str(settings.model_dump())


def test_env_file_is_ignored_by_git() -> None:
    root = Path(__file__).parents[2]
    if not (root / ".git").exists():
        pytest.skip("not a git checkout")

    result = subprocess.run(
        ["git", "check-ignore", ".env"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stdout.strip() == ".env"


def test_mounted_secret_files_win_over_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_file = secrets.token_urlsafe(48)
    from_env = secrets.token_urlsafe(48)
    (tmp_path / "jwt_secret").write_text(from_file + "\n", encoding="utf-8")
    monkeypatch.setenv("JWT_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("PSP_WEBHOOK_SECRET", from_env)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    values = _valid()
    del values["jwt_secret"], values["psp_webhook_secret"]

    settings = load_settings(**values)

    assert settings.jwt_secret.get_secret_value() == from_file
    assert settings.psp_webhook_secret.get_secret_value() == from_env


def test_worker_reads_its_secrets_from_mounted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_key = secrets.token_urlsafe(48)
    database_url = "postgresql+psycopg://toutbet_app:from-file@db:5432/toutbet"
    (tmp_path / "PSP_API_KEY").write_text(api_key, encoding="utf-8")
    (tmp_path / "DATABASE_URL").write_text(database_url, encoding="utf-8")
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)

    settings = load_worker_settings(_env_file=None, psp_base_url="https://psp.test")

    assert settings.psp_api_key.get_secret_value() == api_key
    assert settings.database_url.get_secret_value() == database_url


def test_weak_mounted_secret_refused_without_printing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "psp_api_key").write_text("weak-file-secret", encoding="utf-8")
    monkeypatch.setenv("PSP_API_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    values = _valid()
    del values["psp_api_key"]

    with pytest.raises(ConfigurationError) as error:
        load_settings(**values)

    assert "PSP_API_KEY" in str(error.value)
    assert "weak-file-secret" not in str(error.value)


def test_missing_secrets_directory_is_skipped_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "absent")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert load_settings(**_valid()).env == "prod"
