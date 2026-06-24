# Changelog

All notable changes to this project will be documented in this file.

The format is intentionally simple and human-maintained.

## Unreleased

### Added

- Support for `Hermes CLI` in the local audit and upgrade catalog

### Changed

- `codex` routine upgrade commands now use the built-in `codex update` flow
- `hermes` routine upgrade commands use the built-in `hermes update` flow
- README scope and notes now document Hermes support and self-update behavior

## 2026-06-22

### Added

- Initial public repository split-out from a local tools directory
- `agent_cli_audit.py` for local install-channel, version, and release-risk auditing
- `agent_cli_upgrade.py` for conservative upgrade planning and execution
- `agent_cli_catalog.json` for vendor/channel/source policy data
- `gui.py` NiceGUI prototype with `Overview` and `Console` tabs
- `gui_sample_data.json` for stable example audit presentation
- `requirements-gui.txt` for optional GUI dependencies
- `tooling_class` support to separate `agent-cli` from adjacent `tooling-runtime` dependencies
- Migration advice via `migration_target` and `migration_command`
- Weekly automation support based on local audit and upgrade commands

### Changed

- Tightened `kiro-cli` detection so official script installs are not misclassified as Homebrew-managed casks
- Added `--only-class`, `--only-outdated`, and `--only-nonstandard` audit filters
- Added `--channel recommended|supported|all` upgrade filtering
- Added `--json` output mode to `agent_cli_upgrade.py`
- Added `--offline` mode to `agent_cli_upgrade.py`
- Improved the NiceGUI console so long-running audit and plan calls no longer break the page connection
- The GUI console now surfaces timeout guidance and supports offline planning for faster local checks
- The GUI audit view now respects `recommended` versus `supported` channel selection
- The GUI now includes clearer status strips, more usable filtering, fuller command previews, and a working upgrade-plan summary panel
- The GUI details panel now displays the selected row reliably, including notes, highlights, and action commands

### Project

- Added MIT license
- Consolidated documentation into the main README
- Added minimal GitHub Actions CI
- Added contributing guide and issue/PR templates
- Added a README screenshot of the GUI console
