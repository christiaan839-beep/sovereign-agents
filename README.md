# sovereign-agents

Sovereign Matrix's agent kit: one Claude agent for one small-business job. The agent proposes, the owner says yes,
then the kit acts, and every action leaves a signed receipt.

Version 1 does the first job the design names, supplier inbox + reorders, on a made-up sample business. The design is
in [`docs/design.md`](docs/design.md) and the build plan in [`docs/plans/`](docs/plans/).

## How it works

1. The host starts a run of a job for a business, if the day's spend cap covers it.
2. The agent reads through the job's read tools. Every email, stock record and supplier record reaches it inside a
   fence that marks it as data.
3. The agent stages changes through the job's stage tools. It has no tool that sends, orders or pays. Each staged
   change is checked in code: its input against the tool's schema, its provenance (the supplier, the email and every
   sku were read in this run), and its caps.
4. The run ends with a signed run receipt: its cost, the tools it called, what it staged, what was refused.
5. The owner says yes or no to each staged change. A change expires if no one answers in time.
6. After a yes, the host checks the approval, the expiry and the caps again, claims the change, applies it through
   the connector, and signs an action receipt.

Receipts are in the verifiable-receipts v2 format (Ed25519), signed with the business's own new key. Amounts are whole
cents and spend is whole micro-dollars, so a receipt canonicalises to the same bytes in every language.

## What is where

| Path | What it is |
|---|---|
| `src/sovereign_agents/kit/` | The trust core, with no business specifics. The jobs, engines and command use it only through `sovereign_agents.kit` and `sovereign_agents.kit.testing`. |
| `kit/ledger.py` | SQLite: runs, spend per day, staged changes (idempotency key, expiry, status), decisions, claims, refusals, flags, receipts. |
| `kit/gates.py` | The input, provenance, caps, expiry, approval and tools gates, as plain code. |
| `kit/approvals.py` | `pending`, `decide` (the owner's yes or no, from a host channel) and `apply_approved` (the apply pass). |
| `kit/runner.py` | `Runner` and `RunBudget`: the per-run and per-day caps, and the run receipt. |
| `kit/receipts.py` | `new_keys`, the signer, `issue_receipt`, `verify`, `verify_all`. |
| `kit/fence.py` | The fence for third-party text, copied from commerce-agents (see `NOTICE`). |
| `kit/tools.py`, `kit/guard.py`, `kit/engine.py`, `kit/job.py` | The engine-neutral tool registry, the tool guard, the `Engine` interface, `Job` and `Applier`. |
| `kit/clock.py`, `kit/money.py`, `kit/names.py` | Injected UTC time, model spend as whole micro-dollars, and the business slugs that go into paths and receipts. |
| `kit/testing/` | Receipt checks (Python, and Go when `go` is on the path), a scripted engine, the hostile emails. |
| `src/sovereign_agents/jobs/supplier_reorders/` | The job: `prompt.md`, the read and stage tools, the caps, and the host's applier. |
| `src/sovereign_agents/connectors/` | The `Mailbox`, `Stock` and `Suppliers` interfaces, each with a sample backend that writes to a local outbox. |
| `src/sovereign_agents/engines/agent_sdk.py` | The engine that runs a job on the Claude Agent SDK, in process. |
| `src/sovereign_agents/engines/fake_model.py` | The same engine with a fake model that plays fixed tool calls: no network, no key, no cost. |
| `src/sovereign_agents/cli.py` | The `sovereign-agents` command. |
| `src/sovereign_agents/config.py`, `paths.py`, `formatting.py` | The settings read from the environment, where the kit keeps its state, and how amounts are written for the owner. |
| `samples/sample-bakery/` | A made-up business, labelled as a sample, with the fake model's calls in `fake-model.json`. |
| `tests/`, `tests/kit/`, `evals/` | Unit tests, the end-to-end loop, the attack suite, and the live smoke test. |

## Set up

```
uv sync --locked
```

The kit keeps its state in `SOVEREIGN_AGENTS_HOME` (default `~/.sovereign-agents`): the ledger, each business's keys
under `keys/<business>/`, the sample backends' outboxes under `outbox/<business>/`, and under `sdk-config/` an empty
folder for each run of the Agent SDK's CLI, removed when the run ends. Version 1 keeps each business's private key in a
mode-600 file there. The hosted version keeps it in the host's secret store (design, rule 7).

## Run the sample end to end, with the fake model

The demo makes the sample's keys (once), runs the job with the fake model, asks you yes or no for each staged change,
applies what you approved to the sample outbox, and checks every receipt.

```
uv run sovereign-agents demo
```

The fake model reads the inbox, the stock and the suppliers, stages two reorders, tries a reorder for the invoice in
the hostile email and a `Bash` call (both refused and listed in the run receipt), and reports the hostile email to the
owner. Its calls go through the same hook, tool server, gates and ledger as a real run. Its run receipt names the
engine `fake-model`.

## Run each step with Claude

Claude runs on the client's own Anthropic account, with an API key; the kit never uses a claude.ai login (design, "Who
pays for Claude"). In the build week, with the client: make one workspace for the agent in the client's Claude Console
(not the Default Workspace, which takes no limits), give it a monthly spend limit and an alert at or above the kit's day
cap, and make one API key in it. The key goes straight into the host's secret store, which gives it to the kit as
`ANTHROPIC_API_KEY`. The Agent SDK engine refuses to start without it, and gives the CLI an empty config folder and a
blank `CLAUDE_CODE_OAUTH_TOKEN` for each run.

1. Make the business its own new key pair. The private key is written with mode 600 and never printed.

   ```
   uv run sovereign-agents keys new --business sample-bakery
   ```

2. Run the job once. `SOVEREIGN_AGENTS_MODEL` names the model; when it is unset no model is passed. The run stops at
   `SOVEREIGN_AGENTS_RUN_CAP_MICROS` (default 500000, US$0.50), and starts only when `SOVEREIGN_AGENTS_DAY_CAP_MICROS`
   (default 3000000) covers it plus `SOVEREIGN_AGENTS_MARGIN_MICROS` (default 250000). Add `--engine fake-model` to run
   it with the fake model instead.

   ```
   uv run sovereign-agents run --business sample-bakery --job supplier_reorders
   ```

3. Say yes or no to each staged change. Each change shows its reply whole, exactly as it will be sent. The kit then
   applies the approved ones and signs a receipt for each. A change an earlier apply pass claimed and did not finish
   is listed as not finished, and the kit does not apply it again on its own.

   ```
   uv run sovereign-agents approve --business sample-bakery
   ```

4. Check every receipt in the ledger against the business's public key, with the verifiable-receipts verifier.

   ```
   uv run sovereign-agents receipts verify --business sample-bakery
   ```

## The job's limits

A reorder may name only a known supplier, must answer an email from that supplier's known address that was read in the
run, may name only skus that supplier sells and that were read, has at most 10 lines and at most R5 000.00 in total, and
waits at most 48 hours for a yes. The reply goes only to the supplier's known address. These are `ReorderCaps` in
`jobs/supplier_reorders/caps.py`, checked when the change is staged and again when it is applied.

## Check

```
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q --cov --cov-branch
uv run python scripts/check_coverage.py
```

Branch coverage must stay at 90% or more overall, and at 100% for the ledger, the gates and the receipts. After each
test, `conftest.py` checks every receipt the test left in a ledger with the verifiable-receipts Python verifier, and
with its Go verifier too when `go` is on the path. No test calls the network. CI (`.github/workflows/ci.yml`) runs the
same checks, the demo and actionlint, with a read-only token and actions pinned to commit SHAs.

The live smoke test runs one real job on the client's key and costs at most its run cap:

```
SOVEREIGN_AGENTS_LIVE=1 uv run pytest -q tests/test_live_smoke.py
```

## Not in version 1

The other three jobs, the real connectors (Gmail, Google Calendar, the Shopify Admin API, Google Business Profile), the
WhatsApp approval page and a signed approval, autonomy levels ("Automatic", "Never"), hosting for a client, the
recorded demos for the studio page, and moving the trust core to its own public repo.
