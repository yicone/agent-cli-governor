# Contributing

## Scope

Keep changes narrowly focused on local CLI governance:

- install-channel detection
- latest-version lookup
- release-note summarization
- upgrade and migration planning

Avoid broadening the repository into a general package-manager framework unless the change clearly serves agent CLI governance.

## Development

Run the basic checks before opening a pull request:

```bash
python3 -m py_compile agent_cli_audit.py agent_cli_upgrade.py
python3 agent_cli_audit.py --offline --only-class agent-cli
python3 agent_cli_upgrade.py --channel recommended
```

## Catalog changes

When adding or changing a tracked tool in `agent_cli_catalog.json`:

- prefer official install and changelog sources
- be explicit about `recommended_channels` versus `supported_channels`
- keep custom source parsing minimal and evidence-based
- avoid vendor-specific logic in code when catalog data is sufficient

## Pull requests

- explain the behavioral change
- mention any vendor docs or release pages used for grounding
- call out detection edge cases or backward-compatibility risks
