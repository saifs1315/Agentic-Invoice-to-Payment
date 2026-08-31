from __future__ import annotations

import hashlib
import math
import re


EMBEDDING_DIMENSIONS = 768


def deterministic_embedding(
    text: str,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> list[float]:
    """Stable embedding used only by the explicitly configured test runtime."""
    vector = [0.0] * dimensions
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += -1.0 if digest[4] & 1 else 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
