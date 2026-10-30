# Contributing

Thank you for helping. sovereign-agents is an agent kit for small
businesses: the agent proposes, the owner says yes, then the kit acts and
signs a receipt. `docs/design.md` describes how it fits together.

## Reporting

- **A security flaw**: report it privately, as `SECURITY.md` says. Don't
  open a public issue.
- **A bug**: open an issue with the version or commit, the steps or sample
  data that show it, what you expected and what happened (made-up data and
  test keys only).
- **A proposal**: open an issue first for anything that changes a gate, a
  job's caps, the ledger schema or the receipt contents.

## Changing code

1. Fork, branch from `main`, and open a pull request. CI must pass before
   it merges.
2. Approval, caps, provenance and expiry are checked in code, never left
   to the prompt. The model never gets a tool that applies anything.
3. Amounts are whole cents (integers), never floats.
4. Third-party text reaches the model only inside the fence
   (`src/sovereign_agents/kit/fence.py`).
5. Tests make their keys at run time. No key, token or secret-shaped
   string goes in the repo, a test, a log or the model's context.
6. Code copied from commerce-agents keeps its `Copyright 2026 Anthropic
   PBC` and SPDX headers and is marked as changed; `NOTICE` lists it.
7. Samples are made up and labelled as samples.

## The checks CI runs

```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q --cov --cov-branch          # 90% branch coverage overall
uv run python scripts/check_coverage.py      # the ledger, gates and receipts at 100%
printf 'y\ny\n' | uv run sovereign-agents demo
```

## Style

- Plain, declarative prose in docs and comments; each fact once.
- Commit messages: a subject line under 72 characters, a blank line, then
  what changed and why.
- No model ID or model name in code, tests or commits; the model comes
  from configuration.

By contributing you agree that your contribution is licensed under the
Apache License 2.0 (`LICENSE`).
