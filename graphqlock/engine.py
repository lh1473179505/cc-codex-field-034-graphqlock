"""Authorized GraphQL execution. Not implemented in this snapshot."""

from __future__ import annotations

from typing import Any

from graphqlock.query import Document


def execute(
    schema: dict[str, Any],
    document: Document,
    variables: dict[str, Any],
    principal: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    raise NotImplementedError("execute is not implemented in this snapshot")
