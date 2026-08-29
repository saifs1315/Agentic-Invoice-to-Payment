from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "memory://")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    llm_extraction_enabled: bool = _bool("LLM_EXTRACTION_ENABLED", False)
    llm_explanations_enabled: bool = _bool("LLM_EXPLANATIONS_ENABLED", False)
    erp_mode: str = os.getenv("ERP_MODE", "mock")
    price_tolerance_pct: float = float(os.getenv("MATCH_PRICE_TOLERANCE_PCT", "2.0"))
    quantity_tolerance_pct: float = float(os.getenv("MATCH_QUANTITY_TOLERANCE_PCT", "0.0"))
    total_tolerance_pct: float = float(os.getenv("MATCH_TOTAL_TOLERANCE_PCT", "2.0"))
    max_tax_pct: float = float(os.getenv("MATCH_MAX_TAX_PCT", "25.0"))
    max_freight_pct: float = float(os.getenv("MATCH_MAX_FREIGHT_PCT", "10.0"))
    max_discount_pct: float = float(os.getenv("MATCH_MAX_DISCOUNT_PCT", "30.0"))
    auto_post_enabled: bool = _bool("AUTO_POST_ENABLED", True)
    require_human_approval: bool = _bool("REQUIRE_HUMAN_APPROVAL", False)
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "15"))
    graph_tenant_id: str | None = os.getenv("GRAPH_TENANT_ID")
    graph_client_id: str | None = os.getenv("GRAPH_CLIENT_ID")
    graph_client_secret: str | None = os.getenv("GRAPH_CLIENT_SECRET")
    graph_mailbox: str | None = os.getenv("GRAPH_MAILBOX")
    graph_folder: str = os.getenv("GRAPH_FOLDER", "Inbox")


settings = Settings()
