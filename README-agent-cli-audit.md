# Agent CLI Audit

Audit installed agent CLIs and closely related runtime/tooling dependencies on this machine and report:

- tooling class (`agent-cli` vs `tooling-runtime`)
- detected install path and channel
- current version
- latest version from the install source or upstream npm package
- whether the install path matches the vendor's current recommendation
- the most likely upgrade command

## Usage

```bash
tools/agent_cli_audit.py
tools/agent_cli_audit.py --offline
tools/agent_cli_audit.py --json
tools/agent_cli_audit.py --all
tools/agent_cli_audit.py --only-outdated
tools/agent_cli_audit.py --only-nonstandard
tools/agent_cli_audit.py --only-class agent-cli
tools/agent_cli_audit.py --only-class tooling-runtime
tools/agent_cli_audit.py --with-release-notes
tools/agent_cli_upgrade.py
tools/agent_cli_upgrade.py --channel recommended
tools/agent_cli_upgrade.py --channel supported
tools/agent_cli_upgrade.py --tool codex --tool gemini
tools/agent_cli_upgrade.py --tool uv --apply
```

## Notes

- `--offline` skips network-backed latest-version checks and is better for quick local scans.
- `--only-outdated` only shows installed tools that are both outdated and upgradeable on the current channel.
- `--only-nonstandard` narrows the report to tools whose install channel does not match the vendor's supported/recommended channels.
- `--only-class` lets you separate true Agent CLIs from adjacent tooling/runtime dependencies such as `uv`.
- `--with-release-notes` fetches the latest release notes where possible and produces a simple risk summary. GitHub Releases are supported directly, and a few vendor-hosted changelog pages are summarized heuristically.
- The tool catalog lives in `tools/agent_cli_catalog.json`.
- Most entries are `agent-cli`. A small number of adjacent tools can be retained as `tooling-runtime` when they matter to the same upgrade/governance workflow. `uv` is currently tracked this way.
- The audit output now separates `update_command` from `migration_command`. Use the first for in-channel upgrades, and the second when the current install method should be replaced with the vendor-recommended one.
- `tools/agent_cli_upgrade.py` is the safe wrapper. It only upgrades entries that are both outdated and on a recognized supported/recommended channel.
- `tools/agent_cli_upgrade.py --channel recommended` narrows the plan to vendor-recommended install channels only.
- `tools/agent_cli_upgrade.py --channel supported` is the default and includes both recommended and supported channels.
- `--recommended-only` is kept as a compatibility alias for `--channel recommended`.
- The upgrade plan prints the detected channel status and release risk to make review faster before applying changes.
