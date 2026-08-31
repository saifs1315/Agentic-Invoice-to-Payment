from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

from app.config import Settings
from app.embeddings import EMBEDDING_DIMENSIONS, deterministic_embedding
from app.observability import trace_span


class AIRuntimeUnavailableError(RuntimeError):
    """The mandatory production AI runtime or one of its models is unavailable."""


class AgentProtocolError(RuntimeError):
    """An agent returned output that does not satisfy its bounded contract."""


class SupervisorDecision(BaseModel):
    workflow: Literal["ap_invoice", "ar_remittance", "unknown"]
    action: Literal["DISPATCH_AP", "DISPATCH_AR", "ESCALATE_CLASSIFICATION"]
    reason: str = Field(min_length=3, max_length=1000)
    evidence: list[
        Literal[
            "deterministic_kind",
            "classification_reason",
            "media_type",
            "document_markers",
        ]
    ] = Field(default_factory=list, max_length=4)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DomainAgentDecision(BaseModel):
    action: Literal[
        "RETRIEVE_POLICY",
        "RUN_AP_MATCH",
        "POST_PAYMENT_JOURNAL",
        "RUN_AR_MATCH",
        "APPLY_CASH",
        "ESCALATE",
    ]
    reason: str = Field(min_length=3, max_length=1200)
    retrieval_query: str | None = Field(default=None, min_length=3, max_length=300)
    policy_ids: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[
        Literal[
            "expected_action",
            "query",
            "policy_context",
            "policy_ids",
            "policies",
            "deterministic_result",
            "require_goods_receipt",
            "invoice_status",
            "match",
            "entity_state",
            "idempotency_key",
            "query_seed",
            "control_eligibility",
        ]
    ] = Field(default_factory=list, max_length=5)
    confidence: float | None = Field(default=None, ge=0, le=1)


T = TypeVar("T", bound=BaseModel)


class AgentRuntime(ABC):
    runtime_name: str

    @abstractmethod
    def capabilities(self) -> dict[str, Any]: ...

    @abstractmethod
    def supervise(self, evidence: dict[str, Any]) -> SupervisorDecision: ...

    @abstractmethod
    def decide(
        self,
        *,
        domain: Literal["ap", "ar"],
        stage: str,
        evidence: dict[str, Any],
        allowed_actions: list[str],
    ) -> DomainAgentDecision: ...

    @abstractmethod
    def extract(self, domain: Literal["ap", "ar"], text: str, schema: type[T]) -> T: ...

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...


class OllamaAgentRuntime(AgentRuntime):
    runtime_name = "ollama"

    def __init__(self, settings: Settings) -> None:
        from ollama import Client

        self.settings = settings
        self.client = Client(
            host=settings.ollama_base_url,
            timeout=settings.ollama_timeout_seconds,
        )
        self._embedding_cache: dict[str, list[float]] = {}

    @staticmethod
    def _content(response: Any) -> str:
        if isinstance(response, dict):
            return str(response["message"]["content"])
        return str(response.message.content)

    @staticmethod
    def _validate_json(schema: type[T], content: str) -> T:
        """Accept schema-valid JSON after removing only invalid trailing commas."""
        sanitized: list[str] = []
        in_string = False
        escaped = False
        for index, character in enumerate(content):
            if in_string:
                sanitized.append(character)
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
                sanitized.append(character)
                continue
            if character == ",":
                next_index = index + 1
                while next_index < len(content) and content[next_index].isspace():
                    next_index += 1
                if next_index < len(content) and content[next_index] in "}]":
                    continue
            sanitized.append(character)
        return schema.model_validate_json("".join(sanitized))

    def _structured(self, schema: type[T], system: str, payload: dict[str, Any]) -> T:
        try:
            schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "system",
                    "content": "Return exactly one JSON object and no prose or Markdown. "
                    "The object must validate against this JSON Schema: " + schema_json,
                },
                {"role": "user", "content": json.dumps(payload, default=str)},
            ]
            options = {
                "temperature": 0,
                "num_ctx": self.settings.ollama_context_length,
                "num_predict": 256,
            }
            with trace_span(
                "agent.ollama.chat",
                {"gen_ai.request.model": self.settings.ollama_model},
            ):
                response = self.client.chat(
                    model=self.settings.ollama_model,
                    messages=messages,
                    format="json",
                    options=options,
                    think=False,
                )
                content = self._content(response)
                try:
                    return self._validate_json(schema, content)
                except Exception:
                    repair = self.client.chat(
                        model=self.settings.ollama_model,
                        messages=[
                            *messages,
                            {"role": "assistant", "content": content},
                            {
                                "role": "user",
                                "content": "Your previous object failed the supplied schema. "
                                "Correct it and return only the valid JSON object.",
                            },
                        ],
                        format="json",
                        options=options,
                        think=False,
                    )
                    try:
                        return self._validate_json(schema, self._content(repair))
                    except Exception as repair_exc:
                        details = (
                            repair_exc.errors(include_input=False)
                            if hasattr(repair_exc, "errors")
                            else [{"type": type(repair_exc).__name__}]
                        )
                        raise AgentProtocolError(
                            f"Ollama JSON violated {schema.__name__}: {details}"
                        ) from repair_exc
        except AIRuntimeUnavailableError:
            raise
        except AgentProtocolError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name or name in {
                "ConnectError",
                "ConnectionError",
                "ResponseError",
            }:
                raise AIRuntimeUnavailableError(
                    f"mandatory Ollama runtime is unavailable ({name})"
                ) from exc
            raise AgentProtocolError(f"Ollama returned invalid structured output ({name})") from exc

    def capabilities(self) -> dict[str, Any]:
        result = {
            "runtime": self.runtime_name,
            "reachable": False,
            "generation_model": self.settings.ollama_model,
            "generation_model_ready": False,
            "embedding_model": self.settings.ollama_embedding_model,
            "embedding_model_ready": False,
        }
        try:
            response = self.client.list()
            models = response.get("models", []) if isinstance(response, dict) else response.models
            names = {
                str(model.get("model") if isinstance(model, dict) else model.model)
                for model in models
            }
            normalized_names = names | {
                name.removesuffix(":latest") for name in names
            }
            result["reachable"] = True
            result["generation_model_ready"] = (
                self.settings.ollama_model in normalized_names
            )
            result["embedding_model_ready"] = (
                self.settings.ollama_embedding_model in normalized_names
            )
        except Exception as exc:
            result["error"] = type(exc).__name__
        result["ready"] = bool(
            result["reachable"]
            and result["generation_model_ready"]
            and result["embedding_model_ready"]
        )
        return result

    def supervise(self, evidence: dict[str, Any]) -> SupervisorDecision:
        return self._structured(
            SupervisorDecision,
            "You are the finance supervisor agent. Choose exactly one bounded route from the "
            "document evidence. Never invent fields. If evidence is ambiguous, escalate. "
            "Use only the evidence identifiers allowed by the schema; never copy source text. "
            "The workflow/action pairs are ap_invoice/DISPATCH_AP, "
            "ar_remittance/DISPATCH_AR, and unknown/ESCALATE_CLASSIFICATION.",
            evidence,
        )

    def decide(
        self,
        *,
        domain: Literal["ap", "ar"],
        stage: str,
        evidence: dict[str, Any],
        allowed_actions: list[str],
    ) -> DomainAgentDecision:
        decision = self._structured(
            DomainAgentDecision,
            f"You are the {domain.upper()} finance agent at stage {stage}. Select exactly one "
            f"action from {allowed_actions}. Retrieved policies and deterministic control results "
            "are authoritative. Never bypass a mismatch or approval control. Never copy source "
            "text or JSON fragments into the output. At retrieve_policy, formulate a concise "
            "retrieval_query from the supplied query_seed. At evaluate_match, choose the posting "
            "or cash action only when deterministic_result.matched and "
            "control_eligibility.auto_action_permitted are both true; otherwise ESCALATE. You "
            "may conservatively escalate an eligible case when the policy evidence justifies it. "
            "Cite only policy IDs supplied in evidence.policy_ids and evidence identifiers "
            "allowed by the schema.",
            {"stage": stage, "allowed_actions": allowed_actions, "evidence": evidence},
        )
        if decision.action not in allowed_actions:
            raise AgentProtocolError(
                f"agent action {decision.action} is outside the stage allow-list"
            )
        if stage == "retrieve_policy" and not decision.retrieval_query:
            raise AgentProtocolError("agent did not formulate the required policy retrieval query")
        supplied_policy_ids = {str(item) for item in evidence.get("policy_ids", [])}
        decision.policy_ids = [
            policy_id for policy_id in decision.policy_ids if policy_id in supplied_policy_ids
        ]
        return decision

    def extract(self, domain: Literal["ap", "ar"], text: str, schema: type[T]) -> T:
        return self._structured(
            schema,
            f"Extract the {domain.upper()} document into the supplied schema. Never infer an "
            "identifier or amount that is absent. Return only schema-conformant data.",
            {"document_text": text[:30000]},
        )

    def embed(self, text: str) -> list[float]:
        cached = self._embedding_cache.get(text)
        if cached is not None:
            return list(cached)
        try:
            with trace_span(
                "agent.ollama.embed",
                {"gen_ai.request.model": self.settings.ollama_embedding_model},
            ):
                response = self.client.embed(
                    model=self.settings.ollama_embedding_model,
                    input=text,
                )
            embeddings = (
                response.get("embeddings", [])
                if isinstance(response, dict)
                else response.embeddings
            )
            vector = [float(value) for value in embeddings[0]]
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise AgentProtocolError(
                    f"embedding dimension {len(vector)} does not match pgvector schema "
                    f"dimension {EMBEDDING_DIMENSIONS}"
                )
            self._embedding_cache[text] = vector
            return vector
        except AgentProtocolError:
            raise
        except Exception as exc:
            raise AIRuntimeUnavailableError(
                f"mandatory embedding model is unavailable ({type(exc).__name__})"
            ) from exc


class DeterministicTestAgentRuntime(AgentRuntime):
    """Deterministic test double; Settings prevents its use outside APP_ENV=test."""

    runtime_name = "fake"

    def capabilities(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime_name,
            "reachable": True,
            "generation_model": "deterministic-test-double",
            "generation_model_ready": True,
            "embedding_model": "deterministic-test-double",
            "embedding_model_ready": True,
            "ready": True,
        }

    def supervise(self, evidence: dict[str, Any]) -> SupervisorDecision:
        kind = str(evidence.get("deterministic_kind", "unknown"))
        mapping = {
            "ap_invoice": ("DISPATCH_AP", 0.99),
            "ar_remittance": ("DISPATCH_AR", 0.99),
            "unknown": ("ESCALATE_CLASSIFICATION", 0.5),
        }
        action, confidence = mapping.get(kind, ("ESCALATE_CLASSIFICATION", 0.0))
        return SupervisorDecision(
            workflow=kind if kind in mapping else "unknown",
            action=action,
            reason="Deterministic test agent followed supplied document evidence.",
            evidence=["deterministic_kind", "classification_reason"],
            confidence=confidence,
        )

    def decide(
        self,
        *,
        domain: Literal["ap", "ar"],
        stage: str,
        evidence: dict[str, Any],
        allowed_actions: list[str],
    ) -> DomainAgentDecision:
        if stage == "evaluate_match":
            eligibility = evidence.get("control_eligibility", {})
            auto_action = "POST_PAYMENT_JOURNAL" if domain == "ap" else "APPLY_CASH"
            preferred = (
                auto_action
                if evidence.get("deterministic_result", {}).get("matched")
                and eligibility.get("auto_action_permitted")
                else "ESCALATE"
            )
            evidence_ids = ["deterministic_result", "control_eligibility"]
        else:
            preferred = str(evidence.get("expected_action", allowed_actions[0]))
            evidence_ids = ["expected_action"] if "expected_action" in evidence else []
        action = preferred if preferred in allowed_actions else allowed_actions[0]
        return DomainAgentDecision(
            action=action,
            reason="Deterministic test agent selected the expected guarded action.",
            retrieval_query=(
                str(evidence["query_seed"])
                if stage == "retrieve_policy" and evidence.get("query_seed")
                else None
            ),
            policy_ids=[str(item) for item in evidence.get("policy_ids", [])],
            evidence_ids=evidence_ids,
            confidence=1.0,
        )

    def extract(self, domain: Literal["ap", "ar"], text: str, schema: type[T]) -> T:
        raise AgentProtocolError("test extraction must use structured deterministic fixtures")

    def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)


def create_agent_runtime(settings: Settings) -> AgentRuntime:
    if settings.agent_runtime == "fake":
        return DeterministicTestAgentRuntime()
    return OllamaAgentRuntime(settings)
