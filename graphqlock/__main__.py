"""CLI. inspect already works; execute is the piece to implement."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from graphqlock.query import QueryError, document_to_dict, load_query
from graphqlock.schema import SchemaError, load_schema


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="graphqlock")
    sub = p.add_subparsers(dest="cmd", required=True)
    ins = sub.add_parser("inspect", help="parse a query and summarise the schema")
    ins.add_argument("--schema", required=True)
    ins.add_argument("--query", required=True)
    exe = sub.add_parser("execute", help="authorize and execute a query")
    exe.add_argument("--schema", required=True)
    exe.add_argument("--query", required=True)
    exe.add_argument("--variables", required=False)
    exe.add_argument("--principal", required=True)
    exe.add_argument("--policy", required=True)
    exe.add_argument("--report", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        schema = load_schema(args.schema)
        document = load_query(args.query)
    except (OSError, QueryError, SchemaError) as exc:
        print(f"failed to load query or schema: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "inspect":
        print(json.dumps({
            "schema_query": schema.get("query"),
            "types": list((schema.get("types") or {}).keys()),
            "document": document_to_dict(document),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "execute":
        try:
            policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
            principal = json.loads(Path(args.principal).read_text(encoding="utf-8"))
            variables = {}
            if args.variables:
                variables = json.loads(Path(args.variables).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"failed to load policy, principal, or variables: {exc}", file=sys.stderr)
            return 2
        from graphqlock.engine import execute

        try:
            report = execute(schema, document, variables, principal, policy)
        except NotImplementedError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        decision = str(report.get("decision", "")).lower()
        if decision == "allow":
            return 0
        if decision == "deny":
            return 1
        print("report is missing a decision of allow or deny", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
