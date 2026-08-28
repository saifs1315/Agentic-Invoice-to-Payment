from __future__ import annotations

import os


def configure_observability() -> bool:
    """Enable Phoenix/OpenTelemetry when the optional runtime is present."""
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
    if not endpoint:
        return False
    try:
        from phoenix.otel import register

        register(project_name="ledgerpilot", endpoint=endpoint, auto_instrument=True)
        return True
    except Exception:
        return False

