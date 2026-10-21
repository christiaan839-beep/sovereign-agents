# Releasing sovereign-agents as open source

The founders chose "Open-source the kit" on 21 October 2026 (the studio's decision log): the agent kit is released
openly, like the receipts library, so a client can keep running their agent after they leave. This is the checklist to
get there. Nothing here is done until a founder does it; the repo stays private until then.

## What is already in place

- `LICENSE`: the Apache License, Version 2.0, the canonical text (SHA-256
  `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`), as in verifiable-receipts.
- `NOTICE`: "Copyright 2026 Christiaan de Wet / Sovereign Matrix", the holder the receipts packages and the kit's plan
  already use, and the commerce-agents material.
- `third_party/commerce-agents/LICENSE`: commerce-agents' licence, read from its commit `fd4d592` (byte-identical to the
  canonical Apache-2.0 text; commerce-agents ships no NOTICE file).
- `README.md`, `SECURITY.md` (the contact is a placeholder) and `CONTRIBUTING.md` (it points at the Contributor
  Covenant 2.1 as the code of conduct).

## What the history scan found (21 October 2026)

Every commit on every branch (`git log --all -p`, 10 commits, messages included) was searched with the studio guard's
patterns (`tools/hooks/guard.mjs` in the studio repo: API keys, tokens, cloud keys, private-key blocks, `KEY=`/`TOKEN=`
style assignments, database URLs with passwords, model names and model IDs) and for email addresses, phone and ID
numbers, long hex and base64 strings, session links and local paths.

- **No secret, key, token or private-key block.** The one `-----BEGIN` is a test assertion that a written file starts
  with `-----BEGIN PUBLIC KEY-----`.
- **No model ID and no model name.** Every `claude-` hit is `claude-agent-sdk`, `claude-plugins-official` or the
  `Claude-Session` trailer.
- **No customer or private person's data.** Every email address in a sample or test is on `.example`; the phone-shaped
  hits are `000-000-0000` in a hostile test email and the float `0.0123456789`.
- **Public already:** Christiaan de Wet's name (copyright lines, merge commits) with his GitHub no-reply address, and
  the business mailbox in the plan's quoted `SECURITY.md` for agent-trust-core, which verifiable-receipts publishes too.
- **Internal, not secret:** the commit trailers link to a private Claude Code session
  (`claude.ai/code/session_…`), which no one outside the account can open.

History does not need rewriting for safety. Whether the internal planning material below should be public is the
founders' call, and it decides the route.

## Not fit to be public as it stands

| Where | What | Fix |
|---|---|---|
| `docs/plans/2026-10-16-kit-1-core-and-supplier-reorders.md` | The team's working plan: local paths (`/home/user/…`, 31 lines), the internal commit procedure with the Opsera and Aikido scans and the founders' force-commit (lines 37-41 and the spec-coverage table), the private studio repo's `docs/decisions.md`, a private `facts.md` (6 mentions), session links (18), working branch names, and the table that calls sovereign-agents "private". It also quotes 14 lines of commerce-agents code, now marked and covered by `NOTICE`. | Keep it out of the public repo: move it to the studio repo before publishing and take route B (route A would keep it in history). Or publish it as a dated working record, with a line at its top saying so. |
| `CLAUDE.md` | Points at the studio's private repo (`Sovereign-Matrix-`, its `docs/decisions.md`) and names `sovereign-v2`. Harmless (names only), but a reader cannot follow them. | Keep it as working notes for Claude sessions, or reword to "the studio's decision log" and "any other project". |
| `docs/design.md` | Cited the private decision log by path, called the repo private, and said the receipts signer was "in a pull request under way now". | Done on this branch: cites "the studio's decision log", records the 21 October call, and says the Python package signs and verifies. |
| `README.md` | Opened with "Private." and "in review in the first pull request". | Done on this branch: rewritten for a public reader. |
| Pull requests #1 to #3 | Their titles, descriptions and review comments become public with the repo (route A only). | Read them first; route B leaves them behind. |
| Branches | `claude/sovereign-matrix-studio-h8pvq2` and this branch become public (route A only). | Merge, then delete them before the switch. |

## Only the founders can do these

1. **Copyright holder.** Confirm "Christiaan de Wet / Sovereign Matrix", or change `NOTICE` (and later the file headers)
   to the company's registered name if the code belongs to a company.
2. **Security contact.** Replace the placeholder in `SECURITY.md` with a mailbox that is read. verifiable-receipts uses
   the business mailbox; using it here keeps one contact.
3. **The plan.** Decide whether `docs/plans/` is published (see the table above). That picks the route.
4. **The core and the kit.** The plan splits a public trust core (agent-trust-core) from a private kit. With the kit
   open too, decide whether the split stays (it still keeps the core free of business specifics) or the core folds in.
5. **Merge this branch** into `main`, and choose the route below.

## Route A: make this repo public

Choose this if the plan and the PR threads may be public. History is clean of secrets, so it can stay.

1. Merge this branch into `main` on GitHub. Delete every other branch:
   `git push origin --delete claude/sovereign-matrix-studio-h8pvq2 ccr-fd0fd48b-x2yrl0`.
2. Read pull requests #1 to #3 and any issue: everything in them becomes public.
3. Check forks: `gh api repos/christiaan839-beep/sovereign-agents/forks --jq '.[].full_name'`.
4. On GitHub, open the repo's **Settings → General**. Under **Danger Zone**, choose **Change repository visibility →
   Change to public**, read the warnings and confirm.
5. Then, in **Settings → Code security** (shown as "Code security and analysis" on some accounts): turn on **Secret
   scanning**, **Push protection** and **Private vulnerability reporting**, and Dependabot alerts.
6. In **Settings → Branches** (or **Rules → Rulesets**), protect `main`: pull requests required, no force pushes, no
   deletion.
7. Set the repo's **About**: a one-line description, the topics, and check that GitHub shows "Apache-2.0" as the
   licence.

## Route B: a fresh public repo with clean history

Choose this to keep the plan, the commit trailers and the PR threads private. The private repo stays as the working
record.

1. Merge this branch into `main` of the private repo, and move `docs/plans/` to the studio repo (or remove it in a
   commit on `main`).
2. Export the tree without its history:

   ```bash
   rm -rf /tmp/sovereign-agents-public && mkdir /tmp/sovereign-agents-public
   git -C /home/user/sovereign-agents fetch origin main
   git -C /home/user/sovereign-agents archive origin/main | tar -x -C /tmp/sovereign-agents-public
   cd /tmp/sovereign-agents-public
   grep -rn 'docs/plans' . ; ls docs   # nothing of the plan left; fix the README link if the plan was removed
   ```

3. Run the history scan again on the new tree (one commit) and look over every file once more.
4. Make the first commit:

   ```bash
   git init -b main
   git add -A
   git commit -m "Open-source release of the agent kit"
   ```

5. Create the public repo and push (a founder, logged in to `gh`):

   ```bash
   gh repo create christiaan839-beep/sovereign-agents-public --public \
     --description "One Claude agent for one small-business job: the owner says yes, every action is signed" \
     --source=. --remote=origin --push
   ```

   To keep the name `sovereign-agents`, first rename the private repo (**Settings → General → Repository name**, for
   example to `sovereign-agents-private`), then create the public one as `sovereign-agents`.
6. Apply steps 5 to 7 of route A to the new repo.
7. Point links at the public repo: the studio's pages and docs, and the kit's install line once there is code.

## After either route

- Add a `CODE_OF_CONDUCT.md` with a contact if the founders want enforcement to have a named address.
- From the first code on, the repo's checks (ruff, mypy, pytest and the repo-rules tests that scan for secrets and
  model names) run in CI on every push, with read-only tokens and actions pinned to commit SHAs.
