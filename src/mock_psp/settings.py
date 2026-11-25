from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MockPspSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, hide_input_in_errors=True
    )

    api_key: SecretStr = Field(validation_alias=AliasChoices("PSP_API_KEY"))
    webhook_secret: SecretStr = Field(validation_alias=AliasChoices("PSP_WEBHOOK_SECRET"))
    public_url: str = Field(
        default="http://localhost:8001", validation_alias=AliasChoices("MOCK_PSP_PUBLIC_URL")
    )
    webhook_url: str = Field(
        default="http://localhost:8000/api/webhooks/psp",
        validation_alias=AliasChoices("MOCK_PSP_WEBHOOK_URL"),
    )
    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("MOCK_PSP_HOST"))
    port: int = Field(default=8001, validation_alias=AliasChoices("MOCK_PSP_PORT"))
    refuse_payouts: bool = Field(
        default=False, validation_alias=AliasChoices("MOCK_PSP_REFUSE_PAYOUTS")
    )
    latency_ms: int = Field(default=0, ge=0, validation_alias=AliasChoices("MOCK_PSP_LATENCY_MS"))
    latency_duration_s: float = Field(
        default=0, ge=0, validation_alias=AliasChoices("MOCK_PSP_LATENCY_DURATION_S")
    )
