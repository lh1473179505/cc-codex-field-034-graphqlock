# Execute rules

Reuse `load_schema`, `load_query`, and the AST. Do not call a GraphQL library. Only `query` operations are allowed in this lab (`mutation_denied` if the operation is `mutation`).

`--variables` is a JSON object (default `{}`). `--principal` has `id`, `roles` (list of strings), and `tenant`.

## Schema

`schema.types` maps type name → `{kind: object|scalar, fields: {name: {type, list, non_null, args, auth, cost}}}`.

- `type` is a type name. `list` means the field returns a list. `non_null` is informational for fixtures.
- `auth` if present is `{roles: [...], owner_arg: "id", tenant_field: true}`. A missing `auth` means the field is public.
- `cost` is an integer complexity added when the field is selected (default 1).
- `schema.query` is the root type name (public: `Query`). `schema.data` is a JSON tree used as the in-memory store, keyed like the schema (see fixtures). Resolve object fields from that tree: a field `user(id: ...)` on Query looks up `schema.data.users[id]`; a field `orders` on User looks up `schema.data.orders_by_user[user.id]`. Exact lookup rules are under "Resolution".

## Resolution

Start at the query root. For each selected field (after expanding fragments):

- Resolve the field on the parent type. Unknown field: add an error `{path, message: "unknown_field"}` and omit the field; do not abort the sibling fields. If the field is on the *root* and is unknown, still omit it and record the error; the overall decision is `deny` with `unknown_field` if any such error occurred at any depth? No: unknown fields are request errors and deny `unknown_field`.
- Arguments: values may be literals or `{"var": "name"}` referring to `--variables`. Missing variable is `bad_variable` (deny, no data). Extra args not in the schema field's `args` list are `unknown_arg`.
- `user(id:)` returns `schema.data["users"][id]` or null.
- `users` returns all values of `schema.data["users"]` whose `tenant` equals `principal.tenant` (or all if the principal role includes `admin`).
- `orders` on a User returns `schema.data["orders_by_user"][user["id"]]` or `[]`.
- Scalar fields (`id`, `name`, `email`, `total`, `tenant`, `note`) return the object key of the same name.
- Lists: map the subselection over each item.

## Authorization

Before reading a field's value, check `auth`:

- If `roles` is set, the principal must have at least one of those roles (`field_forbidden`). `admin` may only bypass this when `policy.admin_bypass` is true (public: false — even admins must have the role listed, or the field is forbidden).
- If `owner_arg` is set (e.g. `"id"`), the argument of that name must equal `principal.id`, unless the principal has role `admin` *and* `policy.admin_bypass_owner` is true (public: true for owner checks only).
- If `tenant_field` is true, the parent object's `tenant` key must equal `principal.tenant` (admins with `admin_bypass_owner` may skip this too).

A forbidden field is omitted from `data` and an error `{path, message: "field_forbidden"}` is appended. **Sibling fields that are allowed must still appear.** Do not replace the entire parent object with null unless the *parent* itself failed. Do not leak the forbidden value into `errors` (no actual emails, notes, or totals in error messages).

## Limits and fragments

- Expand fragment spreads and inline fragments against the parent type. A spread on a type that does not match (inline `on` / fragment type condition not equal to the parent type and not a name in `schema.implements[parent]`) is skipped, not an error.
- Unknown fragment name is `unknown_fragment` (deny).
- If expanding a fragment would re-enter the same fragment name on the current expansion stack, deny `fragment_cycle` and do not execute.
- Depth is the nesting of fields (root fields are depth 1). Crossing `policy.max_depth` (public: 5) is `too_deep`.
- Complexity: sum of field `cost` (default 1) times the number of aliases of that field at that parent, plus list multipliers: a list field multiplies the cost of its subtree by `policy.list_factor` (public: 10) unless the field has an argument `first` (int), in which case use `min(first, policy.list_factor)`. Crossing `policy.max_complexity` (public: 50) is `too_complex`.
- Alias batching: at a given parent, the number of selected fields that resolve to the *same* schema field name (via alias or not) must be `<= policy.max_aliases_per_field` (public: 3). Over is `alias_batch`.

## Decision and report

- `allow` only if the query executed, no deny-level reason codes were raised, and at least the document was valid. Field-level `field_forbidden` **is** a deny (`decision` is `deny`) but `data` still contains the allowed siblings. This is intentional: the report must show partial data plus errors so a buggy implementation that either leaks or hides everything can be marked down.
- `too_deep`, `too_complex`, `alias_batch`, `fragment_cycle`, `unknown_field`, `unknown_fragment`, `unknown_arg`, `bad_variable`, `mutation_denied` are also deny, and for those you may set `data` to null (do not execute further once a document-level cap or cycle is hit).
- `reason_codes` lists the unique codes.
- `data` is the result object (or null).
- `errors` is a list of `{path, message}` where path is a list of keys/indices.
- Exit 0 on allow, 1 on deny, 2 if files cannot be read or the query cannot be parsed.

## Public fixtures

`--schema fixtures/schemas/shop.json --policy fixtures/policy.json --principal fixtures/principals/NAME.json --query fixtures/queries/NAME.graphql` (and `--variables fixtures/variables/NAME.json` when that file exists):

- `ok-user` (principal `alice`) → allow, data contains alice's `id` and `name` (not `email` — that field is not selected)
- `forbidden-email` (principal `alice`, query selects `email`) → deny `field_forbidden`, `data.user.id` and `data.user.name` still present, `email` omitted, errors mention `field_forbidden` but not the email value
- `ok-admin-users` (principal `admin`) → allow, lists users in the admin's tenant only? admin's tenant is `shop`; with `admin_bypass_owner` they may see the shop tenant users
- `too-deep` → deny `too_deep`
- `alias-batch` → deny `alias_batch` (four aliases of `name`)
- `fragment-cycle` → deny `fragment_cycle`
- `ok-fragment` (principal `alice`) → allow, fragment used once to select `name`
- `complexity` → deny `too_complex` (users + nested orders without `first`)
