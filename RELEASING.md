# Releasing

A release is a `vX.Y.Z` tag that matches `version` in `pyproject.toml` and points at a commit on `main`. Pushing the
tag starts `.github/workflows/release.yml`, which:

1. checks the tag against `pyproject.toml` and `main`;
2. runs the CI workflow;
3. builds the sdist and wheel with `uv build` and records a signed build-provenance attestation for each;
4. waits for a maintainer to approve the `release` environment, then creates the GitHub release with the files and
   the attestation bundle (`attestation.sigstore.json`). The notes are the version's section of `CHANGELOG.md`.

## A release

1. Set `version` in `pyproject.toml`, move the `Unreleased` notes in `CHANGELOG.md` under the new version, and merge
   the pull request.
2. Tag the merge commit, signed, and push the tag:

   ```
   git tag -s v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```

3. Approve the `release` deployment in the Actions run.

## Checking a release

```
gh attestation verify sovereign_agents-0.1.0-py3-none-any.whl --repo christiaan839-beep/sovereign-agents
```

## PyPI

Not yet. The kit depends on verifiable-receipts by git commit, and PyPI refuses packages with direct URL dependencies.
Once `sovereign-matrix-verifiable-receipts` is on PyPI, the dependency becomes a version range and a publish job with
trusted publishing (no stored token) joins the workflow.

## One-time setup

Create the `release` environment (Settings → Environments) with yourself as a required reviewer, and allow only `v*`
tags to deploy to it.
