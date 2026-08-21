# Changelog

All notable changes to this project will be documented in this file.

The format is intentionally simple and human-maintained.

## Unreleased

### Added

- `agent-operations` is a separate catalog class for agent orchestration, routing, workspace, and observability products; Multica, Claude Code Router, Orca, and CodexBar are tracked in this class
- Google Jules CLI, Agent Browser, and OpenSpec are now tracked from their installed npm distributions
- Linked source checkouts can report their local Git relation through a resolved executable path without fetching or modifying the checkout
- Cursor Agent CLI (`agent`) is now tracked as an official script-installed CLI with installer-backed version checks and `agent update` guidance
- xAI Grok CLI and ACPX are now tracked; Grok is an `agent-cli`, while ACPX is a separate `tooling-runtime`
- `--check-node-runtime` adds a read-only Node runtime drift check for npm upgrade safety
- `--inventory-private-harnesses` adds a separate, opt-in inventory of explicit private Agent Harness evidence and client bindings without recursive app scanning, host execution, secret output, or automatic remediation
- The GUI now exposes a `Load release notes` control, enabled by default for online audits
- Nori, Vercel fx, Codex ACP, 9Router, and Nowledge Mem CLI are now tracked with explicit class boundaries
- 9Router source-linked installs report local checkout and upstream-ref distance without fetching or changing Git state

### Changed

- 9Router and Nowledge Mem CLI are classified as `agent-operations`: they operate an agent routing or knowledge-management plane, rather than providing a protocol or execution runtime
- App-bundled CLIs can fall back to the bundle version when their command does not expose a parseable version
- npm upgrade commands now run through `mise exec node -- ...`; a failed runtime drift check warns during dry runs and blocks npm-channel `--apply` operations without modifying the environment
- Node runtime checks now recognize `mise`, `nvm`, `fnm`, and `asdf`; validated non-mise providers use a path-bound npm command rather than a bare npm invocation
- CLI subprocess output is decoded as UTF-8 with replacement so a malformed tool version string cannot fail unrelated audit entries or GUI JSON parsing
- GUI controls reserve enough width for the Plan scope label
- GUI audits now reuse matching in-memory results for 10 minutes, while the adjacent `Force refresh` action explicitly bypasses the cache; no audit data is persisted
- Overview now explains the three tool roles plus runtime-safety and private-Harness boundaries, matching the current governance model
- JSON HTTP lookups replace malformed UTF-8 bytes before parsing, preventing one malformed upstream response from emitting repeated item warnings
- Class filtering now happens before probes begin, preventing warnings from unrelated tools and exposing catalog revision metadata for audit diagnostics

## [0.2.0] - 2026-08-08

### Added

- Hermes CLI and Google Antigravity CLI (`agy`) are now tracked by the local audit and upgrade catalog
- Antigravity version checks use its platform-specific official manifest; its background self-update behavior is described without treating an interactive CLI launch as an automatic upgrade

### Changed

- Upgrade plans now present one channel-aware primary action, its rationale, and its installation-channel effect; informational alternatives are never run by `--apply`
- npm and Homebrew installations retain package-manager ownership when a CLI's native self-update behavior cannot be verified; Kilo Code and OpenCode use explicit `--method` arguments to preserve their detected method
- Claude Code script installations use `claude update`; Homebrew-managed installations retain their package-manager upgrade path
- A Codex binary detected inside ChatGPT.app is identified as app-bundled and advises updating ChatGPT.app, while standalone script installations retain the official standalone installer path
- Codex standalone version checks now use the official GitHub release tag rather than npm registry metadata
- Audit output now distinguishes install channel from binary container, avoiding false channel-drift reports for script-installed shims that resolve into app bundles
- Online audits are more resilient: checks run concurrently, inherit configured system proxy settings for Python, npm, and Homebrew, and report bounded lookup failures as warnings instead of blocking the whole audit
- Timed-out local probes now stop their entire process group, preventing background child processes from delaying later audits
- The GUI no longer blocks an audit on release-note retrieval; selected CLI details load notes on demand, and matching audit results are reused when generating an upgrade plan
- GUI plan scope and table filters now have separate effects: plan scope only changes plan generation, while table filters do not alter the audit summary baseline
- The GUI accepts `--port` when the default `8080` is already in use
- English and Simplified Chinese READMEs now document the supported CLI scope, installation-channel model, and update behavior

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
