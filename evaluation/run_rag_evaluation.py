from __future__ import annotations

# ruff: noqa: E402 - add the repository root before importing the application package.

import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.context import ContextRetriever
from app.agent_runtime import AIRuntimeUnavailableError, create_agent_runtime
from app.config import Settings
from app.repository import MemoryRepository, POLICIES


ROOT = Path(__file__).resolve().parent
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")


async def evaluate() -> dict[str, object]:
    from ragas import SingleTurnSample
    from ragas.metrics import NonLLMContextPrecisionWithReference, NonLLMContextRecall

    dataset = json.loads((ROOT / "policy_dataset.json").read_text(encoding="utf-8"))
    config = Settings()
    runtime = create_agent_runtime(config)
    retriever = ContextRetriever(MemoryRepository(runtime.embed), runtime, config)
    if retriever.index is None:
        raise AIRuntimeUnavailableError(
            "RAG evaluation requires the live LlamaIndex/Ollama semantic index"
        )
    precision_metric = NonLLMContextPrecisionWithReference()
    recall_metric = NonLLMContextRecall()
    rows = []
    for item in dataset:
        reference_ids = item["reference_context_ids"]
        retrieved = retriever.retrieve_with_ids(
            item["query"],
            top_k=max(1, len(reference_ids)),
        )
        if item.get("expect_abstain"):
            abstained = not retrieved
            rows.append(
                {
                    "query": item["query"],
                    "retrieved_context_ids": [policy_id for policy_id, _ in retrieved],
                    "reference_context_ids": [],
                    "context_precision": 1.0 if abstained else 0.0,
                    "context_recall": 1.0 if abstained else 0.0,
                    "abstained": abstained,
                }
            )
            continue
        reference_contexts = [
            POLICIES[int(policy_id.removeprefix("policy-")) - 1]
            for policy_id in reference_ids
        ]
        sample = SingleTurnSample(
            user_input=item["query"],
            retrieved_contexts=[policy for _, policy in retrieved],
            reference_contexts=reference_contexts,
        )
        precision = await precision_metric.single_turn_ascore(sample)
        recall = await recall_metric.single_turn_ascore(sample)
        rows.append(
            {
                "query": item["query"],
                "retrieved_context_ids": [policy_id for policy_id, _ in retrieved],
                "reference_context_ids": reference_ids,
                "context_precision": float(precision),
                "context_recall": float(recall),
            }
        )

    metrics = {
        "dataset_size": len(rows),
        "ragas_non_llm_context_precision": round(
            sum(row["context_precision"] for row in rows) / len(rows), 4
        ),
        "ragas_non_llm_context_recall": round(
            sum(row["context_recall"] for row in rows) / len(rows), 4
        ),
        "cases": rows,
        "notes": (
            "Offline RAGAS non-LLM context precision and recall over paraphrased, multi-policy, "
            "and out-of-domain cases using the required local Ollama embedding model; no external "
            "AI API is used."
        ),
    }
    output = ROOT / "results" / "rag-latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if (
        metrics["ragas_non_llm_context_precision"] < 0.75
        or metrics["ragas_non_llm_context_recall"] < 0.75
    ):
        raise SystemExit("policy retrieval evaluation did not meet the 0.75 quality threshold")
    return metrics


if __name__ == "__main__":
    print(json.dumps(asyncio.run(evaluate()), indent=2))
