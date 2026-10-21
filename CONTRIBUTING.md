# Contributing

Thank you for your interest. The kit is in design and nothing is built yet, so the most useful contribution now is an
issue: a question about the design, a gap in the trust model, or an attack the kit should withstand. Please open an
issue before you start a pull request, so the work fits the plan.

What every change must keep:

- Nothing is applied without the owner's yes, and the model never gets a tool that applies anything. Approval, caps,
  provenance and expiry are checked in code.
- No key, token or secret-shaped string in the repo, a test or a log. Tests make their keys at run time.
- No model ID or model name in code, tests or commits. The model comes from configuration.
- Amounts are whole cents (integers), never floats.
- Third-party text reaches the model only inside the fence.
- Samples are made up and labelled as samples: no real business, person or customer.
- Code copied from another project keeps its copyright and SPDX headers, is marked as changed, and ships its licence
  in `third_party/`.

Report security problems privately, as [`SECURITY.md`](SECURITY.md) says, not in an issue.

By contributing, you agree that your contribution is licensed under the Apache License, Version 2.0, as section 5 of
the [licence](LICENSE) states.

Everyone taking part is expected to follow the
[Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/), version 2.1.
