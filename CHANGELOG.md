# Changelog

## Unreleased

- verifiable-receipts moves to commit f41348e (its Python package 0.3.0), in the package and in the Go verifier the
  tests check receipts with. Its verifiers follow the strict Ed25519 profile of VAOS 2.0 section 7.3: they refuse a
  key or an `R` that is not a canonical encoding of a point of order L, a key of mixed order included, and an `S` of
  L or more. Receipts the kit signs pass it in Python, Go and JavaScript.
- `load_public_pem` and `load_signer` refuse a public key those verifiers refuse, with their reason, and
  `public_key_problem` gives that reason for any PEM public key. A key `new_keys` makes is never refused.

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
