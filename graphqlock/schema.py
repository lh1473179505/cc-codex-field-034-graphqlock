"""Load a lab GraphQL schema JSON. Execution is not implemented here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaError(Exception):
    pass


def load_schema(path: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(str(exc)) from exc
    if "types" not in raw or "query" not in raw:
        raise SchemaError("schema must have types and query")
    return raw
