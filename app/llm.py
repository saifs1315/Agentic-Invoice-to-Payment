from __future__ import annotations

import json
from typing import Any

from app.config import Settings


class DecisionExplainer:
    """Ollama-backed explanation layer; never participates in posting eligibility."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def explain(self, decision: dict[str, Any]) -> str:
        if not self.settings.llm_explanations_enabled:
            return "Explanation generation disabled; deterministic variance codes are authoritative."
        try:
            from llama_index.llms.ollama import Ollama

            llm = Ollama(model=self.settings.ollama_model, base_url=self.settings.ollama_base_url, request_timeout=30)
            prompt = (
                "Explain this invoice match decision to a finance reviewer in no more than three sentences. "
                "Use only the supplied JSON. Do not recommend bypassing a control.\n"
                + json.dumps(decision, default=str)
            )
            return str(llm.complete(prompt)).strip()
        except Exception as exc:
            return f"Explanation unavailable ({type(exc).__name__}); deterministic variance codes remain authoritative."

