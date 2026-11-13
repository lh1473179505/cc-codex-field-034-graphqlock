"""Authorized GraphQL execution over the schema's in-memory data tree."""

from __future__ import annotations

from typing import Any, Iterator

from graphqlock.query import Document, Field, FragSpread, Inline

Selection = Field | FragSpread | Inline


def _report(
    decision: str,
    reason_codes: list[str],
    data: Any,
    errors: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason_codes": reason_codes,
        "data": data,
        "errors": errors,
        "notes": notes or [],
    }


def execute(
    schema: dict[str, Any],
    document: Document,
    variables: dict[str, Any],
    principal: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    variables = variables or {}
    policy = policy or {}
    if document.operation != "query":
        return _report("deny", ["mutation_denied"], None, [])

    types = schema.get("types") or {}
    root_type = schema.get("query")
    implements = schema.get("implements") or {}
    max_depth = int(policy.get("max_depth", 5))
    max_complexity = int(policy.get("max_complexity", 50))
    list_factor = int(policy.get("list_factor", 10))
    max_aliases = int(policy.get("max_aliases_per_field", 3))

    codes: list[str] = []
    errors: list[dict[str, Any]] = []

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    def type_matches(cond: str, parent_type: str) -> bool:
        if cond == parent_type:
            return True
        return cond in (implements.get(parent_type) or [])

    def expand(
        sel: list[Selection], parent_type: str, stack: list[str]
    ) -> Iterator[Field]:
        for node in sel:
            if isinstance(node, Field):
                yield node
            elif isinstance(node, FragSpread):
                frag = document.fragments.get(node.name)
                if frag is None:
                    add("unknown_fragment")
                    continue
                if node.name in stack:
                    add("fragment_cycle")
                    continue
                if not type_matches(frag.type_cond, parent_type):
                    continue
                yield from expand(frag.sel, parent_type, stack + [node.name])
            elif isinstance(node, Inline):
                if node.type_cond is None or type_matches(node.type_cond, parent_type):
                    yield from expand(node.sel, parent_type, stack)

    def resolve_args(node: Field) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in node.args.items():
            if isinstance(value, dict) and "var" in value:
                var_name = value["var"]
                if var_name not in variables:
                    add("bad_variable")
                    continue
                out[key] = variables[var_name]
            else:
                out[key] = value
        return out

    def fields_of(type_name: str) -> dict[str, Any]:
        return (types.get(type_name) or {}).get("fields") or {}

    # ---- static validation: depth, complexity, alias batch, fragments ----
    complexity = 0

    def walk(
        sel: list[Selection],
        parent_type: str,
        depth: int,
        mult: int,
        stack: list[str],
        path: list[Any],
    ) -> None:
        nonlocal complexity
        fields = fields_of(parent_type)
        nodes = list(expand(sel, parent_type, stack))
        counts: dict[str, int] = {}
        for node in nodes:
            counts[node.name] = counts.get(node.name, 0) + 1
        if any(count > max_aliases for count in counts.values()):
            add("alias_batch")
        for node in nodes:
            key = node.alias or node.name
            node_path = path + [key]
            fdef = fields.get(node.name)
            if fdef is None:
                add("unknown_field")
                errors.append({"path": node_path, "message": "unknown_field"})
                continue
            allowed_args = fdef.get("args") or []
            for arg_name in node.args:
                if arg_name not in allowed_args:
                    add("unknown_arg")
                    errors.append({"path": node_path, "message": "unknown_arg"})
            args = resolve_args(node)
            if depth > max_depth:
                add("too_deep")
            complexity += int(fdef.get("cost", 1)) * mult
            child_mult = mult
            if fdef.get("list"):
                first = args.get("first")
                if isinstance(first, int) and not isinstance(first, bool):
                    factor = min(first, list_factor)
                else:
                    factor = list_factor
                child_mult = mult * factor
            if node.sel:
                walk(node.sel, fdef.get("type"), depth + 1, child_mult, stack, node_path)

    walk(document.sel, root_type, 1, 1, [], [])
    if complexity > max_complexity:
        add("too_complex")
    if codes:
        return _report("deny", codes, None, errors)

    # ---- execution with per-field authorization ----
    roles = principal.get("roles") or []
    is_admin = "admin" in roles
    admin_bypass = bool(policy.get("admin_bypass"))
    admin_bypass_owner = bool(policy.get("admin_bypass_owner"))
    store = schema.get("data") or {}

    def permitted(fdef: dict[str, Any], args: dict[str, Any], parent_obj: Any) -> bool:
        auth = fdef.get("auth")
        if not auth:
            return True
        required = auth.get("roles")
        if required and not (set(required) & set(roles)):
            if not (is_admin and admin_bypass):
                return False
        owner_arg = auth.get("owner_arg")
        if owner_arg and args.get(owner_arg) != principal.get("id"):
            if not (is_admin and admin_bypass_owner):
                return False
        if auth.get("tenant_field"):
            parent_tenant = parent_obj.get("tenant") if isinstance(parent_obj, dict) else None
            if parent_tenant != principal.get("tenant"):
                if not (is_admin and admin_bypass_owner):
                    return False
        return True

    def resolve_value(
        parent_type: str, name: str, args: dict[str, Any], parent_obj: Any
    ) -> Any:
        if parent_type == root_type:
            if name == "user":
                return (store.get("users") or {}).get(args.get("id"))
            if name == "users":
                all_users = list((store.get("users") or {}).values())
                if is_admin:
                    return all_users
                return [
                    u
                    for u in all_users
                    if isinstance(u, dict) and u.get("tenant") == principal.get("tenant")
                ]
        if parent_type == "User" and name == "orders" and isinstance(parent_obj, dict):
            return (store.get("orders_by_user") or {}).get(parent_obj.get("id")) or []
        if isinstance(parent_obj, dict):
            return parent_obj.get(name)
        return None

    def project(node: Field, fdef: dict[str, Any], value: Any, path: list[Any]) -> Any:
        if value is None or not node.sel:
            return value
        child_type = fdef.get("type")
        if fdef.get("list"):
            if not isinstance(value, list):
                return []
            items = []
            for index, item in enumerate(value):
                child: dict[str, Any] = {}
                run(node.sel, child_type, item, path + [index], child)
                items.append(child)
            return items
        if isinstance(value, dict):
            child = {}
            run(node.sel, child_type, value, path, child)
            return child
        return value

    def run(
        sel: list[Selection], parent_type: str, parent_obj: Any, path: list[Any], out: dict
    ) -> None:
        fields = fields_of(parent_type)
        for node in expand(sel, parent_type, []):
            key = node.alias or node.name
            fdef = fields.get(node.name)
            if fdef is None:
                continue
            args = resolve_args(node)
            node_path = path + [key]
            if not permitted(fdef, args, parent_obj):
                add("field_forbidden")
                errors.append({"path": node_path, "message": "field_forbidden"})
                continue
            value = resolve_value(parent_type, node.name, args, parent_obj)
            out[key] = project(node, fdef, value, node_path)

    result: dict[str, Any] = {}
    run(document.sel, root_type, None, [], result)
    if codes:
        notes = ["partial data: forbidden fields omitted, allowed siblings retained"]
        return _report("deny", codes, result, errors, notes)
    return _report("allow", [], result, errors)
