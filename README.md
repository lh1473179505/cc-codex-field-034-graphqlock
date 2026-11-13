# graphqlock

A GraphQL executor with field-level authorization, depth and complexity caps, alias batching limits, and fragment-cycle detection. Errors must not leak sibling fields that the principal cannot read.

`python -m graphqlock inspect --schema fixtures/schemas/shop.json --query fixtures/queries/ok-user.graphql` already shows the AST. `execute` is still a stub.

Standard library only. No Docker, no graphql-core, no network.
