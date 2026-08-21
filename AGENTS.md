# agent-cli-governor Contributor Guidance

## Scope

Keep this repository focused on local governance of Agent CLIs and adjacent
runtime tooling: installation-channel detection, version evidence, release-risk
summaries, and conservative upgrade or migration plans. Do not turn it into a
general package-manager framework.

## Evidence And Classification

- Prefer vendor documentation, official package registries, official manifests,
  and local command/configuration evidence. Do not infer an install channel or a
  Client-to-Harness binding from `command -v` alone.
- Keep `normalized_channel` separate from `binary_container`. A script shim may
  resolve to an app bundle without changing the script installation channel.
- Classify a CLI that directly accepts and executes a user coding-agent task as
  `agent-cli`; classify protocol adapters and execution dependencies as
  `tooling-runtime`; classify orchestration, routing, workspace, and
  observability products that coordinate several agents as `agent-operations`.
- A desktop application is a Client or host, not automatically an Agent Harness.
  Report a private Harness only when a fixed known path or explicit host binding
  provides evidence.

## Read-Only Boundaries

- `agent_cli_audit.py` is read-only. `--check-node-runtime` and
  `--inventory-private-harnesses` remain independent operations.
- Private-Harness inventory may inspect only documented fixed host paths and
  explicitly safe configuration keys. Do not recursively scan app bundles, run
  host-private executables, output command arguments or secrets, or persist
  machine inventory data in the repository.
- An unconfirmed host or a discovered private copy is not itself a governance
  risk. Report a risk only when an actual Client binding and a specific policy
  conflict or known risk are both evidenced.
- `agent_cli_upgrade.py --apply` changes the machine. Never run it without
  explicit user authorization. Keep dry-run output available when runtime
  validation blocks execution.
- Preserve existing dirty-worktree changes unless they directly conflict with
  the requested work. Do not use repository cleanup commands as a substitute
  for understanding their ownership.

## Subprocess And Release-Note Contracts

- CLI JSON is an interface consumed by the GUI. Keep machine-readable output on
  stdout, return a JSON object for expected non-zero checks, and put diagnostics
  in structured payload fields or stderr rather than mixing them into JSON.
- Decode CLI and package-manager subprocess output explicitly as UTF-8 with
  replacement. A malformed version string must become an item-level warning,
  not fail unrelated entries or make the GUI's JSON parser fail.
- Online GUI audits load release notes by default; offline audits never fetch
  them. Keep release-note failures bounded and item-scoped, preserving version
  and channel results. `unknown` release risk means evidence was unavailable,
  not that an upgrade is safe.

## Node Runtime And GUI

- Preserve npm ownership through the validated runtime-provider abstraction;
  never silently fall back to bare `npm` for an unsupported runtime manager.
- A terminal `--check-node-runtime` validates that terminal environment only.
  The GUI's **Run Node Runtime Check** action launches the same check from the
  GUI server process and is the correct validation when GUI context matters.
  `python3 gui.py` is intentionally blocking; no second command needs to run in
  that terminal.
- Keep the GUI a thin, non-mutating shell over the CLI/JSON interfaces. Do not
  add an action that silently upgrades, migrates, or scans private host data.
- `Plan scope` controls only generated upgrade plans. Do not use it to hide
  audit-table rows; table filtering remains explicit through result filters.

## Verification

Before finishing relevant changes, run:

```bash
python3 -m py_compile agent_cli_audit.py agent_cli_upgrade.py gui.py
python3 -m unittest discover -s tests
python3 agent_cli_audit.py --offline --only-class agent-cli
python3 agent_cli_upgrade.py --channel recommended
```

For private-Harness work also run:

```bash
python3 agent_cli_audit.py --inventory-private-harnesses --json
```

For GUI command or rendering changes, start the GUI on an unused port and
verify in a real browser that an online audit returns catalog entries, warnings
remain item-specific, Release Risk contains fetched evidence where available,
and **Check Node Runtime** completes from the GUI process context.

Document vendor evidence, compatibility assumptions, and any intentionally
unconfirmed host-specific behavior in the change description.
