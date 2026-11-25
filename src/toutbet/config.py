import base64
import binascii
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Request
from pydantic import SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

MIN_SECRET_BYTES = 32
ED25519_SEED_BYTES = 32
SECRETS_DIR = Path("/run/secrets")


class ConfigurationError(Exception):
    pass


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    database_url: SecretStr = SecretStr("sqlite:///./toutbet.db")
    database_admin_url: SecretStr | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, file_secret_settings, env_settings, dotenv_settings


def reject_empty(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        raise ValueError("is missing")
    return value


def require_long(value: SecretStr) -> SecretStr:
    if len(value.get_secret_value().encode("utf-8")) < MIN_SECRET_BYTES:
        raise ValueError(f"is too weak, at least {MIN_SECRET_BYTES} bytes are required")
    return value


def require_http_url(value: str, info: ValidationInfo) -> str:
    if not value.startswith(("https://", "http://")):
        raise ValueError("must be an http(s) URL")
    if info.data.get("env") == "prod" and not value.startswith("https://"):
        raise ValueError("must use https when ENV=prod")
    return value.rstrip("/")


class WorkerSettings(DatabaseSettings):
    env: Literal["dev", "test", "prod"] = "prod"
    psp_base_url: str
    psp_api_key: SecretStr

    @field_validator("psp_api_key", mode="before")
    @classmethod
    def reject_empty_api_key(cls, value: Any) -> Any:
        return reject_empty(value)

    @field_validator("psp_api_key")
    @classmethod
    def require_long_api_key(cls, value: SecretStr) -> SecretStr:
        return require_long(value)

    @field_validator("psp_base_url")
    @classmethod
    def require_http_psp_url(cls, value: str, info: ValidationInfo) -> str:
        return require_http_url(value, info)


class Settings(WorkerSettings):
    jwt_secret: SecretStr
    receipt_signing_key: SecretStr
    psp_webhook_secret: SecretStr
    public_base_url: str
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost"]
    cors_origins: Annotated[list[str], NoDecode] = []
    trusted_proxy_ips: Annotated[list[str], NoDecode] = ["127.0.0.1"]

    rate_limit_login: str = "5/minute"
    rate_limit_register: str = "5/hour"
    rate_limit_refresh: str = "30/minute"
    rate_limit_write: str = "20/minute"
    rate_limit_read: str = "120/minute"
    rate_limit_write_per_ip: str = "200/minute"
    rate_limit_read_per_ip: str = "1200/minute"
    rate_limit_kyc: str = "5/hour"
    rate_limit_withdrawals: str = "5/hour"
    rate_limit_invitation_accept: str = "10/minute"
    rate_limit_webhook: str = "300/minute"

    bet_creation_enabled: bool = True
    stakes_enabled: bool = True
    deposits_enabled: bool = True
    withdrawals_enabled: bool = True

    @field_validator("allowed_hosts", "cors_origins", "trusted_proxy_ips", mode="before")
    @classmethod
    def split_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def forbid_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("must list explicit origins, '*' is not allowed")
        return value

    @field_validator("jwt_secret", "receipt_signing_key", "psp_webhook_secret", mode="before")
    @classmethod
    def reject_empty_secret(cls, value: Any) -> Any:
        return reject_empty(value)

    @field_validator("jwt_secret", "psp_webhook_secret")
    @classmethod
    def require_long_secret(cls, value: SecretStr) -> SecretStr:
        return require_long(value)

    @field_validator("receipt_signing_key")
    @classmethod
    def require_ed25519_seed(cls, value: SecretStr) -> SecretStr:
        decode_seed(value.get_secret_value())
        return value

    @field_validator("public_base_url")
    @classmethod
    def require_http_public_url(cls, value: str, info: ValidationInfo) -> str:
        return require_http_url(value, info)


def decode_seed(encoded: str) -> bytes:
    try:
        seed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("must be a base64url-encoded 32-byte Ed25519 seed") from None
    if len(seed) != ED25519_SEED_BYTES:
        raise ValueError("must be a base64url-encoded 32-byte Ed25519 seed")
    return seed


def describe_validation_error(error: ValidationError) -> str:
    problems = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        name = "_".join(str(part) for part in item["loc"]).upper()
        if item["type"] == "missing":
            reason = "is missing"
        else:
            reason = str(item["msg"]).removeprefix("Value error, ")
        problems.append(f"{name} {reason}")
    return "Invalid configuration: " + "; ".join(problems)


def mounted_secrets(overrides: dict[str, Any]) -> dict[str, Any]:
    return {"_secrets_dir": SECRETS_DIR if SECRETS_DIR.is_dir() else None, **overrides}


def load_settings(**overrides: Any) -> Settings:
    try:
        return Settings(**mounted_secrets(overrides))
    except ValidationError as error:
        raise ConfigurationError(describe_validation_error(error)) from None


def load_database_settings(**overrides: Any) -> DatabaseSettings:
    try:
        return DatabaseSettings(**mounted_secrets(overrides))
    except ValidationError as error:
        raise ConfigurationError(describe_validation_error(error)) from None


def load_worker_settings(**overrides: Any) -> WorkerSettings:
    try:
        return WorkerSettings(**mounted_secrets(overrides))
    except ValidationError as error:
        raise ConfigurationError(describe_validation_error(error)) from None


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings
