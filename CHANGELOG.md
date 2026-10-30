# Changelog

## Unreleased

## 0.1.0

The first release: the kit and its first job, supplier inbox + reorders, on the sample bakery.

- The kit: a ledger of runs, staged changes and receipts; the approval, provenance, cap and expiry gates, checked in
  code; the fence around third-party text; a daily spend cap and a time limit per run; an Ed25519 receipt for every
  applied change, checked with verifiable-receipts.
- The supplier inbox + reorders job: read tools for the inbox, stock levels and suppliers; stage tools for a reorder
  with its reply and for flagging a message; the reorder caps in `jobs/supplier_reorders/caps.py`.
- `sovereign-agents demo` runs the job end to end on the sample with a fake model; the live smoke test runs it with
  Claude.
- Licensed under Apache-2.0.

It installs from this repository, not from PyPI: it depends on verifiable-receipts by commit until that package is
published.
