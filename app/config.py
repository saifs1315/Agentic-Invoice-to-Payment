from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded afresh from environment for each instance."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    app_env: str = "development"
    database_url: str = "memory://"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    llm_extraction_enabled: bool = False
    llm_explanations_enabled: bool = False
    erp_mode: Literal["mock", "http"] = "mock"
    erp_base_url: str = "http://localhost:8080"
    erp_timeout_seconds: float = Field(default=5.0, gt=0)
    price_tolerance_pct: float = Field(
        default=2.0,
        ge=0,
        validation_alias="MATCH_PRICE_TOLERANCE_PCT",
    )
    quantity_tolerance_pct: float = Field(
        default=0.0,
        ge=0,
        validation_alias="MATCH_QUANTITY_TOLERANCE_PCT",
    )
    total_tolerance_pct: float = Field(
        default=2.0,
        ge=0,
        validation_alias="MATCH_TOTAL_TOLERANCE_PCT",
    )
    max_tax_pct: float = Field(default=25.0, ge=0, validation_alias="MATCH_MAX_TAX_PCT")
    max_freight_pct: float = Field(
        default=10.0,
        ge=0,
        validation_alias="MATCH_MAX_FREIGHT_PCT",
    )
    max_discount_pct: float = Field(
        default=30.0,
        ge=0,
        validation_alias="MATCH_MAX_DISCOUNT_PCT",
    )
    max_monetary_amount: Decimal = Decimal("1000000000.00")
    auto_post_enabled: bool = True
    require_human_approval: bool = False
    max_upload_mb: int = Field(default=15, gt=0)
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: str | None = None
    graph_mailbox: str | None = None
    graph_folder: str = "Inbox"

    @field_validator("max_monetary_amount", mode="before")
    @classmethod
    def validate_max_monetary_amount(cls, value: object) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(
                "MAX_MONETARY_AMOUNT must be a valid finite decimal number"
            ) from exc
        if not parsed.is_finite():
            raise ValueError("MAX_MONETARY_AMOUNT must be a valid finite decimal number")
        if parsed <= 0:
            raise ValueError("MAX_MONETARY_AMOUNT must be greater than zero")
        return parsed


settings = Settings()
