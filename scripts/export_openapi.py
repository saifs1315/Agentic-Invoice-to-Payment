from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ROOT = PROJECT_ROOT / "openapi"


def export() -> None:
    finance_app = import_module("app.api").app
    mock_erp_app = import_module("app.mock_erp_api").app
    ROOT.mkdir(exist_ok=True)
    contracts = {
        "openapi.yaml": finance_app.openapi(),
        "mock-erp-openapi.yaml": mock_erp_app.openapi(),
    }
    for filename, schema in contracts.items():
        (ROOT / filename).write_text(
            yaml.safe_dump(schema, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )


if __name__ == "__main__":
    export()
