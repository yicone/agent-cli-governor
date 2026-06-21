# Changelog

All notable changes to this project will be documented in this file.

The format is intentionally simple and human-maintained.

## Unreleased

- No unreleased changes yet.

## 2026-06-22

### Added

- Initial public repository split-out from a local tools directory
- `agent_cli_audit.py` for local install-channel, version, and release-risk auditing
- `agent_cli_upgrade.py` for conservative upgrade planning and execution
- `agent_cli_catalog.json` for vendor/channel/source policy data
- `tooling_class` support to separate `agent-cli` from adjacent `tooling-runtime` dependencies
- Migration advice via `migration_target` and `migration_command`
- Weekly automation support based on local audit and upgrade commands

### Changed

- Tightened `kiro-cli` detection so official script installs are not misclassified as Homebrew-managed casks
- Added `--only-class`, `--only-outdated`, and `--only-nonstandard` audit filters
- Added `--channel recommended|supported|all` upgrade filtering

### Project

- Added MIT license
- Consolidated documentation into the main README
- Added minimal GitHub Actions CI
- Added contributing guide and issue/PR templates
