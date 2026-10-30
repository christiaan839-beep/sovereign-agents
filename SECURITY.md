# Security

## Reporting a vulnerability

Report a suspected flaw privately, through a GitHub security advisory:
<https://github.com/christiaan839-beep/sovereign-agents/security/advisories/new>
(the repository's **Security** tab, then **Report a vulnerability**).
Don't open a public issue for it.

If you cannot use GitHub, email `sovereign.systems007@gmail.com` with
`[security]` at the start of the subject.

Please include the version or commit, what you expected and what happened,
and the sample data, ledger or receipts that show it, using test keys and
made-up data only, never a key or a business's data in real use.

You should get a first reply within seven days. A fix is developed in the
private advisory, and the advisory is published with the release that
fixes it, crediting you unless you ask otherwise.

## Scope

In scope: anything that lets a change be applied without the owner's yes;
lets a staged change pass the provenance, cap or expiry gates it should
fail; lets text from an email, order or supplier record act as an
instruction rather than data; puts a key, token or credential into the
model's context, a log or a receipt; lets a run spend past its day's cap;
or leaves an applied change without a receipt that verifies, or a receipt
that verifies for a change that was not applied.

Out of scope: the quality of the model's proposals (the owner reviews
every one), a business key or connector credential that is itself
compromised, and vulnerabilities in dependencies that do not affect this
code (report those upstream).

## Supported versions

Fixes go into `main` and the latest release; older versions are not
patched.
