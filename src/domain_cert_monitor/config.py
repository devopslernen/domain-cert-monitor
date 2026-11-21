"""Settings from the environment (and ``.env``). Names follow SPEC.md → Deployment."""

from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    port: int = 8080
    base_url: str = "http://localhost:8080"
    check_interval_hours: float = 6
    alert_thresholds: Annotated[list[int], NoDecode] = [30, 14, 7]
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_from: str = ""
    alert_to: Annotated[list[str], NoDecode] = []
    tz: str = "UTC"
    log_level: str = "info"

    # Internal; not in .env.example.
    database_path: str = "/data/monitor.db"
    static_dir: str = "/app/static"

    @field_validator("smtp_security", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("alert_thresholds", mode="before")
    @classmethod
    def _split_thresholds(cls, value: object) -> object:
        return _split(value)

    @field_validator("alert_thresholds")
    @classmethod
    def _check_thresholds(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("at least one threshold is required")
        if any(t <= 0 for t in value):
            raise ValueError("thresholds must be positive")
        return sorted(set(value))

    @field_validator("alert_to", mode="before")
    @classmethod
    def _split_recipients(cls, value: object) -> object:
        return _split(value)

    @field_validator("check_interval_hours")
    @classmethod
    def _check_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("tz")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from and self.alert_to)
