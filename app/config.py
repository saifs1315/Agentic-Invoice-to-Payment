from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    agent_runtime: Literal["ollama", "fake"] = "ollama"
    ollama_model: str = "qwen3.5:2b-q4_K_M"
    ollama_embedding_model: str = "embeddinggemma"
    ollama_timeout_seconds: float = Field(default=300.0, gt=0)
    ollama_context_length: int = Field(default=8192, ge=2048, le=32768)
    agent_max_steps: int = Field(default=8, ge=6, le=20)
    rag_similarity_threshold: float = Field(default=0.05, ge=-1, le=1)
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

    @model_validator(mode="after")
    def reject_fake_runtime_outside_tests(self) -> "Settings":
        if self.agent_runtime == "fake" and self.app_env.lower() != "test":
            raise ValueError("AGENT_RUNTIME=fake is permitted only when APP_ENV=test")
        return self

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
