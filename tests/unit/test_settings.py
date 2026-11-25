import secrets
import subprocess
from pathlib import Path
from typing import Any

import pytest

from support import random_seed
from toutbet.config import ConfigurationError, load_settings

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
    for name in (*SECRET_NAMES, "PSP_BASE_URL", "PUBLIC_BASE_URL", "CORS_ORIGINS"):
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
