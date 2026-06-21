# agent-cli-governor

Audit and govern locally installed agent CLIs and adjacent runtime tooling.

`agent-cli-governor` is a small local-ops repository for three related problems:

- detecting what agent CLIs are installed on a machine
- checking whether the current install channel still matches vendor guidance
- producing conservative upgrade and migration suggestions

## What It Provides

- `agent_cli_audit.py`
  Inspects installed tools, detects install channel, compares current versus latest version, summarizes release risk, and surfaces migration advice when the install method drifts from vendor guidance.
- `agent_cli_upgrade.py`
  Builds a conservative upgrade plan from the audit results and can optionally execute approved upgrades.
- `agent_cli_catalog.json`
  The policy catalog that defines tracked tools, install channels, and source-specific latest-version lookups.

## Scope

The main focus is `agent-cli` tools such as:

- OpenAI Codex CLI
- Claude Code
- Gemini CLI
- Kiro CLI
- Devin CLI
- Copilot CLI
- OpenCode
- Multica
- Amp
- Droid
- Kilo Code
- Cline

The catalog can also retain a small number of adjacent `tooling-runtime` dependencies, such as `uv`, when they matter to the same local governance workflow.

## Requirements

- macOS or another environment where the tracked CLIs are available in `PATH`
- Python 3
- Optional but commonly expected on the target machine:
  - `brew`
  - `npm`
  - network access for latest-version and release-note checks

No Python package installation is currently required.

## Usage

### Audit

```bash
python3 agent_cli_audit.py
python3 agent_cli_audit.py --offline
python3 agent_cli_audit.py --json
python3 agent_cli_audit.py --all
python3 agent_cli_audit.py --only-outdated
python3 agent_cli_audit.py --only-nonstandard
python3 agent_cli_audit.py --only-class agent-cli
python3 agent_cli_audit.py --only-class tooling-runtime
python3 agent_cli_audit.py --with-release-notes
```

### Upgrade Planning

```bash
python3 agent_cli_upgrade.py
python3 agent_cli_upgrade.py --channel recommended
python3 agent_cli_upgrade.py --channel supported
python3 agent_cli_upgrade.py --tool codex --tool gemini
python3 agent_cli_upgrade.py --tool uv --apply
```

## Output Model

The audit output distinguishes:

- `tooling_class`
  `agent-cli` versus `tooling-runtime`
- `normalized_channel`
  For example `script`, `npm`, `brew-core`, `brew-cask`, `desktop-install`
- `channel_status`
  `recommended`, `supported`, or `nonstandard`
- `update_command`
  The conservative in-channel upgrade path
- `migration_command`
  A suggested migration path when the current install channel is no longer preferred

## Notes

- `--offline` skips network-backed latest-version checks and is better for quick local scans.
- `--only-outdated` only shows installed tools that are both outdated and upgradeable on the current channel.
- `--only-nonstandard` narrows the report to tools whose install channel does not match the vendor's supported or recommended channels.
- `--only-class` lets you separate true Agent CLIs from adjacent tooling/runtime dependencies such as `uv`.
- `--with-release-notes` fetches the latest release notes where possible and produces a simple risk summary. GitHub Releases are supported directly, and a few vendor-hosted changelog pages are summarized heuristically.
- The tool catalog lives in `agent_cli_catalog.json`.
- Most entries are `agent-cli`. A small number of adjacent tools can be retained as `tooling-runtime` when they matter to the same upgrade/governance workflow.
- The audit output separates `update_command` from `migration_command`. Use the first for in-channel upgrades, and the second when the current install method should be replaced with the vendor-recommended one.
- `agent_cli_upgrade.py` only upgrades entries that are both outdated and on a recognized supported or recommended channel.
- `agent_cli_upgrade.py --channel recommended` narrows the plan to vendor-recommended install channels only.
- `agent_cli_upgrade.py --channel supported` is the default and includes both recommended and supported channels.
- `--recommended-only` is kept as a compatibility alias for `--channel recommended`.

## Repository Development

Basic health checks:

```bash
python3 -m py_compile agent_cli_audit.py agent_cli_upgrade.py
python3 agent_cli_audit.py --offline --only-class agent-cli
python3 agent_cli_upgrade.py --channel recommended
```

## License

MIT. See [LICENSE](./LICENSE).
