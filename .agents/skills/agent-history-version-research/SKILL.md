---
name: agent-history-version-research
description: Research historical install versions for coding agents in coding-agent-kit. Use when a task asks for an agent's version at a past date, for building timepoint version snapshots, or for turning upstream release history into cakit install selectors.
---

# Agent History Version Research

Use this skill when you need a `cakit install <agent> --version ...` value for a past date.

## Workflow

1. Normalize the target date first.
   Use an explicit `YYYY-MM-DD` date and a concrete cutoff timestamp.
   Default cutoff: `YYYY-MM-DDT23:59:59Z` unless the user gives a timezone.

2. Identify the install selector type before querying history.
   Read the agent implementation in `src/agents/<agent>.py` and confirm whether the selector is:
   - npm package version
   - PyPI package version
   - GitHub release tag
   - git ref / commit
   - CDN / installer-managed binary version

3. Query the upstream source that matches the selector type.
   Do not assume the README example version or the marketing changelog version is the install selector.

4. Filter to installable versions.
   Keep the exact selector that cakit should pass.
   Exclude nightly, preview, platform-suffixed, or dist-tag-only variants unless that is the actual install selector.

5. Record the result with provenance.
   For each row keep:
   - `timepoint_date`
   - `cutoff_utc`
   - `agent`
   - `install_version`
   - `status` (`confirmed`, `inferred`, `pending`)
   - `source_kind`
   - `source_ref`
   - `published_at_utc`
   - `note`

6. If the selector is still unresolved, write partial progress instead of waiting.
   Mark uncertain rows as `inferred` or `pending` and explain the blocker in `note`.

## Source Selection

- npm agents: use the npm registry `time` map and filter to plain installable versions.
- PyPI agents: use `releases[*].upload_time_iso_8601` and filter to plain installable versions.
- GitHub release agents: use official releases and `published_at`, not repo commits.
- Git ref agents: use the latest exact commit or tag on/before the cutoff.
- CDN agents: inspect the official installer first; if there is no public version index, probe exact URLs and use `Last-Modified`.

## Agent Notes

- `codex`: filter out platform-suffixed package versions like `-linux-x64`.
- `continue`: historical versions should come from `@continuedev/cli` package history; the shell installer is only the transport.
- `openclaw`: historical versions should come from the `openclaw` npm package; the installer resolves versions through npm.
- `qoder`: use `@qoder-ai/qodercli` npm history, not the shell manifest's current `latest`.
- `copilot`, `goose`, `opencode`: use GitHub releases, then normalize the string the way cakit expects.
- `claude`: use the public GCS bucket listing for `claude-code-releases/`.
- `factory`, `trae-cn`: use exact binary URLs plus `HEAD`/`Last-Modified` when no public release index is available.
- `cursor`: distinguish public product releases (`2.5`, `2.6`) from the agent install build ID; if the build ID is unknown, record the public release as `inferred` and keep researching.
- `trae-oss`: use a commit hash on/before the cutoff if there is no matching release/tag stream.

## References

- For the per-agent source matrix and query rules, read [references/source-matrix.md](references/source-matrix.md).
- The reusable command-line helper uses `ghapi` for GitHub history queries. One-time setup:
  `source .venv/bin/activate && python -m ensurepip && python -m pip install ghapi`
- Then run:
  `source .venv/bin/activate && python references/query_install_versions.py --timepoint-date YYYY-MM-DD --agent <agent...>`.
- For currently saved snapshot work, inspect [tests/install_script_version_snapshots.tsv](/root/coding-agent-kit/tests/install_script_version_snapshots.tsv).
