# Changelog

All notable changes to this project will be documented in this file.

The format is intentionally simple and human-maintained.

## Unreleased

### Added

- Support for `Hermes CLI` in the local audit and upgrade catalog
- Support for Google Antigravity CLI (`agy`), including platform-specific official manifest checks and background self-update guidance

### Changed

- Upgrade plans now show one channel-aware primary action with its rationale and installation-channel effect; alternatives are informational and never run by `--apply`
- npm/Homebrew installs now keep package-manager ownership when native self-update behavior is unverified; Kilo Code and OpenCode use explicit `--method` arguments to preserve their detected method
- Claude Code now uses `claude update`, while an app-bundled Codex CLI directs users to update ChatGPT.app instead of proposing a standalone CLI command
- GUI audits no longer block on release-note retrieval; release notes load on demand for the selected CLI, and generated upgrade plans reuse the matching audit result when available
- GUI timeouts now stop their child process groups, preventing timed-out audits from continuing in the background and causing later requests to contend
- Online audits now probe up to four CLIs concurrently, bound HTTP/package-manager lookups, and report unreachable version sources as audit warnings instead of consuming the whole GUI timeout
- npm and Homebrew version lookups now inherit the proxy resolved from macOS system settings when no explicit proxy environment is set
- Codex standalone installs now compare against the official GitHub release tag instead of relying on npm registry metadata
- Audit results now distinguish install channel from binary container, so script-installed shims that resolve into app bundles do not appear as channel drift
- GUI plan scope, results filters, and release-note loading now have separate contexts: plan scope only affects plan generation, table filters do not alter audit summaries, and release notes load from the selected CLI's Details panel
- Timed-out local probe commands now terminate their entire process groups, preventing launcher processes from leaving native children behind
- The GUI now accepts `--port` so it can run when the default `8080` is occupied
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
