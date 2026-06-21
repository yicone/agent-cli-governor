# agent-cli-governor

Audit and govern locally installed agent CLIs and adjacent runtime tooling.

This repository currently provides:

- `agent_cli_audit.py`: inspect installed tools, detect install channel, compare current vs latest version, summarize release risk, and surface migration advice when the install method drifts from vendor guidance
- `agent_cli_upgrade.py`: build a conservative upgrade plan from the audit results and optionally execute approved upgrades
- `agent_cli_catalog.json`: the policy catalog that defines supported tools, install channels, and source-specific latest-version lookups

## Scope

The main focus is `agent-cli` tools such as Codex, Claude Code, Gemini CLI, Kiro CLI, Devin CLI, and similar agent-facing CLIs.

The catalog can also retain a small number of adjacent `tooling-runtime` dependencies, such as `uv`, when they matter to the same local governance workflow.

## Usage

```bash
python3 agent_cli_audit.py --offline --only-class agent-cli
python3 agent_cli_audit.py --with-release-notes --only-outdated --only-class agent-cli
python3 agent_cli_upgrade.py --channel recommended
python3 agent_cli_upgrade.py --channel supported
```

See [`README-agent-cli-audit.md`](./README-agent-cli-audit.md) for more examples and behavior notes.
