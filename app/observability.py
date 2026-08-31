from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator


logger = logging.getLogger(__name__)


def configure_observability() -> bool:
    """Enable Phoenix/OpenTelemetry for the required production runtime."""
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
    if not endpoint:
        return False
    try:
        from phoenix.otel import register

        register(project_name="ledgerpilot", endpoint=endpoint, auto_instrument=True)
        return True
    except Exception:
        logger.exception("Phoenix/OpenTelemetry configuration failed")
        return False


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Create an explicit finance-agent span without coupling callers to OTel."""
    try:
        from opentelemetry import trace

        with trace.get_tracer("ledgerpilot.agents").start_as_current_span(name) as span:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            yield span
    except ImportError:
        yield None
