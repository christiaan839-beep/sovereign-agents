# sovereign-agents

An agent kit from Sovereign Matrix: one Claude agent for one small-business job. The agent proposes, the owner says
yes, then the kit acts, and every action leaves a signed receipt.

The kit is open source so that a business can keep running its agent on its own host, with its own signing key in its
own secret store, whether or not it keeps working with us.

## Status

In design. Nothing is built yet: there is no code to install or run. The design is in
[`docs/design.md`](docs/design.md), and the plan for the first version is in [`docs/plans/`](docs/plans).

## The trust model

- **The owner's yes.** The agent can read and stage a change. It has no tool that applies one. The kit applies a
  change only after the owner approves it, in code the model cannot reach.
- **Checks in code, not in the prompt.** Approval, spend caps, expiry and provenance (an action may touch only what
  the agent read in that run) are checked by the kit before anything happens.
- **Third-party text is data.** Emails, reviews and order notes reach the model inside a fence that marks them as
  quoted text. An instruction inside one is reported to the owner, never followed.
- **Receipts.** Every run and every applied action is signed with the business's own Ed25519 key, in the
  [verifiable-receipts](https://github.com/christiaan839-beep/verifiable-receipts) v2 format. Anyone with the public
  key can check what was done, when, and that the record was not changed.
- **Keys stay with the business.** Each business makes its own key pair. The private key and the service credentials
  stay in the host's secret store; they never enter the repo, a log or the model's context.

## The first jobs

Supplier inbox and reorders first, then bookings and no-show reminders, Shopify orders and follow-ups, and review
replies. Each job is demonstrated on a made-up sample business, labelled as a sample.

## Licence

Apache License, Version 2.0: see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Material from
[commerce-agents](https://github.com/anthropics/commerce-agents) keeps its copyright notice, and its licence is in
[`third_party/commerce-agents/`](third_party/commerce-agents).

## Security and contributing

Report a vulnerability privately as [`SECURITY.md`](SECURITY.md) says. To contribute, see
[`CONTRIBUTING.md`](CONTRIBUTING.md).
