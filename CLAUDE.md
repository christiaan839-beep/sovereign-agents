# sovereign-agents: working notes for Claude

Sovereign Matrix's agent kit: one Claude agent for one small-business job. The agent proposes, the owner says yes, then
the kit acts and signs a receipt. Read `docs/design.md` first. The founders' calls live in the studio repo's
`docs/decisions.md` (Sovereign-Matrix-); where this repo's docs and its code disagree, the code is right.

## Rules that always hold

- Nothing is applied without the owner's yes, and the model never gets a tool that applies anything. Approval, caps,
  provenance and expiry are checked in code, never left to the prompt.
- Keys: new keys only (the founders, 16 October 2026). Never reuse a key from sovereign-v2 or anywhere else. No private
  key, API key, token or secret-shaped string in the repo, a test, a log or a model's context; tests make their keys at
  run time. Credentials are read by the connector at the edge.
- Never put a model ID (anything starting `claude-`) or a model name in a commit, a file or a test; the model comes
  from configuration. Commit messages end with `Co-Authored-By: Claude <noreply@anthropic.com>` and the session link.
- Amounts are whole cents (integers), never floats: receipts must canonicalise byte-for-byte across languages.
- Third-party text (emails, reviews, order notes) reaches the model only inside the fence.
- Code copied from commerce-agents keeps its `Copyright 2026 Anthropic PBC` and SPDX headers, is marked as changed, and
  its licence ships in `third_party/commerce-agents/`.
- Samples are made up and labelled as samples: no real business, person or customer.
- In `.github/workflows` never put `${{ github.event.* }}` text into `run:`; pass it through `env:`. Read-only tokens,
  actions pinned to commit SHAs.
- Never weaken TLS or unset `HTTPS_PROXY`.
- Prose: plain declarative sentences, each fact once, no hype words.

## How to build and check

Version 1 is built: the kit and the supplier inbox + reorders job, on the sample bakery. Set up with `uv sync --locked`,
then run the checks CI runs: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -q
--cov --cov-branch` (90% overall) and `uv run python scripts/check_coverage.py` (the ledger, gates and receipts at
100%). The tests check every receipt they leave with the receipts library's verifiers.
