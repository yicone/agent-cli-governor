#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "agent_cli_catalog.json"
SEMVER_RE = re.compile(r"\bv?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b")
GITHUB_RELEASES_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/?$")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HIGH_RISK_TERMS = [
    "breaking",
    "deprecated",
    "deprecate",
    "removed",
    "remove",
    "migration",
    "migrate",
    "incompatible",
    "compatibility",
    "auth",
    "authentication",
    "sandbox",
    "config",
    "configuration",
    "protocol",
    "permission",
    "security",
]
HTTP_TIMEOUT_SECONDS = 8
HTTP_ATTEMPTS = 2
PACKAGE_MANAGER_TIMEOUT_SECONDS = 12
AUDIT_WORKERS = 4
NODE_RUNTIME_COMMANDS = ("node", "npm", "npx", "pnpm")
DEFAULT_HARNESS_ENTRYPOINTS = ("codex", "agent", "node", "npm", "npx", "pnpm")
PRIVATE_HARNESS_HOSTS = (
    ("zed", "Zed", Path("/Applications/Zed.app"), Path.home() / ".config/zed/settings.json"),
    ("devin-desktop", "Devin Desktop", Path("/Applications/Devin.app"), Path.home() / ".config/devin/config.json"),
    ("multica", "Multica", Path("/Applications/Multica.app"), Path.home() / ".multica/config.json"),
    ("codeg", "Codeg", Path("/Applications/codeg.app"), Path.home() / ".codeg"),
    ("conductor", "Conductor", Path("/Applications/Conductor.app"), Path.home() / ".conductor/settings.toml"),
)
MEDIUM_RISK_TERMS = [
    "hook",
    "plugin",
    "install",
    "update",
    "runtime",
    "provider",
    "session",
    "tool",
    "approval",
    "storage",
]


def run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 20,
    check: bool = False,
    use_system_proxy: bool = False,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if use_system_proxy:
        # npm and Homebrew do not read macOS proxy settings themselves. Bridge the
        # proxy selected by urllib into the conventional child-process variables.
        for name, value in system_proxy_env().items():
            merged_env.setdefault(name, value)
    if env:
        merged_env.update(env)
    process = subprocess.Popen(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged_env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)

    completed = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            args,
            output=stdout,
            stderr=stderr,
        )
    return completed


def system_proxy_env() -> dict[str, str]:
    proxies = urllib.request.getproxies()
    http_proxy = proxies.get("http")
    https_proxy = proxies.get("https") or http_proxy
    proxy_env: dict[str, str] = {}
    if http_proxy:
        proxy_env.update({"HTTP_PROXY": http_proxy, "http_proxy": http_proxy})
    if https_proxy:
        proxy_env.update({"HTTPS_PROXY": https_proxy, "https_proxy": https_proxy})
    return proxy_env


def command_output(args: list[str]) -> tuple[str | None, str | None]:
    try:
        completed = run(args, timeout=15)
    except Exception as exc:
        return None, str(exc)
    if completed.returncode != 0:
        return None, (completed.stderr or completed.stdout or "command failed").strip()
    return completed.stdout.strip(), None


def command_runtime_path(command_path: str | None) -> str | None:
    """Resolve the target of a simple local wrapper without executing shell code."""
    if not command_path:
        return None
    path = Path(command_path)
    if path.is_symlink():
        return resolve_path(command_path)
    if path.is_file():
        try:
            match = re.search(r"^exec\s+([^\s]+)", path.read_text(), re.MULTILINE)
            if match:
                return resolve_path(match.group(1))
        except OSError:
            pass
    return resolve_path(command_path)


def node_runtime_provider(node_path: str | None) -> dict[str, Any]:
    """Identify a supported Node manager from the active Node executable path."""
    resolved = resolve_path(node_path) if node_path else ""
    if shutil.which("mise"):
        mise_node, _ = command_output(["mise", "which", "node"])
        if mise_node and resolve_path(mise_node) == resolved:
            return {"id": "mise", "execution": "mise-exec", "executable": True}
    for provider, marker in {
        "nvm": "/.nvm/versions/node/",
        "fnm": "/.local/share/fnm/node-versions/",
        "asdf": "/.asdf/installs/nodejs/",
    }.items():
        if marker in resolved:
            return {"id": provider, "execution": "path-bound", "executable": True}
    return {"id": "unsupported", "execution": None, "executable": False}


def node_execution_prefix(runtime_drift: dict[str, Any] | None = None) -> str | None:
    """Return a validated Node execution environment without a command suffix."""
    runtime = runtime_drift or check_node_runtime()
    if runtime.get("status") != "pass" or not runtime.get("executable"):
        return None
    provider = safe_dict(runtime.get("runtime_provider"))
    if provider.get("id") == "mise":
        return "mise exec node --"
    command_paths = safe_dict(runtime.get("runtime_paths"))
    node_path = command_paths.get("node")
    npm_path = command_paths.get("npm")
    if not node_path or not npm_path:
        return None
    node_bin = str(Path(resolve_path(node_path)).parent)
    return f"env PATH={shlex.quote(node_bin)}:$PATH"


def npm_command_prefix(runtime_drift: dict[str, Any] | None = None) -> str | None:
    """Return a manager-bound npm command only after the runtime topology passes."""
    prefix = node_execution_prefix(runtime_drift)
    if not prefix:
        return None
    runtime = runtime_drift or check_node_runtime()
    npm_path = safe_dict(runtime.get("runtime_paths")).get("npm")
    if not npm_path:
        return None
    if safe_dict(runtime.get("runtime_provider")).get("id") == "mise":
        return f"{prefix} npm"
    return f"{prefix} {shlex.quote(npm_path)}"


def check_node_runtime() -> dict[str, Any]:
    """Read-only validation of a supported Node runtime for npm upgrades."""
    command_paths = {command: shutil.which(command) for command in NODE_RUNTIME_COMMANDS}
    runtime_paths = {command: command_runtime_path(path) for command, path in command_paths.items()}
    result: dict[str, Any] = {
        "status": "unsupported",
        "runtime_provider": {"id": "unresolved", "execution": None, "executable": False},
        "executable": False,
        "mise_paths": {},
        "command_paths": command_paths,
        "runtime_paths": runtime_paths,
        "local_entries": {},
        "versions": {},
        "issues": [],
        "gui_probe_required": (
            "A terminal probe validates only its own environment. Use the GUI's Run Node Runtime Check "
            "action to validate the GUI server process; it launches this probe from that process."
        ),
    }
    node_path = runtime_paths.get("node")
    npm_path = runtime_paths.get("npm")
    if not node_path or not npm_path:
        result["status"] = "drift"
        result["issues"].append("node and npm must both be available on PATH")
        return result

    provider = node_runtime_provider(node_path)
    result["runtime_provider"] = provider
    if provider["id"] == "unsupported":
        result["issues"].append(
            "Node runtime provider is not supported for executable npm upgrades; use dry-run only"
        )
    if provider["id"] == "mise":
        for command in NODE_RUNTIME_COMMANDS:
            output, error = command_output(["mise", "which", command])
            if not output:
                result["status"] = "drift"
                result["issues"].append(f"mise which {command} failed: {error or 'no path returned'}")
            else:
                result["mise_paths"][command] = output.splitlines()[0]

    for command, command_path in command_paths.items():
        if not command_path:
            result["status"] = "drift"
            result["issues"].append(f"command -v {command} found no executable")
            continue
        version, version_error = command_output([command_path, "--version"])
        if version:
            result["versions"][command] = version.splitlines()[0]
        elif version_error:
            result["status"] = "drift"
            result["issues"].append(f"{command} --version failed: {version_error}")

        if provider["id"] != "mise":
            continue
        mise_path = result["mise_paths"].get(command)
        if not mise_path:
            continue
        local_entry = Path.home() / ".local/bin" / command
        local_data: dict[str, Any] = {"path": str(local_entry), "exists": local_entry.exists() or local_entry.is_symlink()}
        local_wrapper_matches = False
        if local_entry.is_symlink():
            local_data["is_symlink"] = True
            local_data["target"] = os.readlink(local_entry)
            result["status"] = "drift"
            result["issues"].append(f"{local_entry} is a symlink; generic mise entry policy requires a wrapper")
        else:
            local_data["is_symlink"] = False
            if local_entry.is_file():
                try:
                    wrapper_match = re.search(r"^exec\s+([^\s]+)", local_entry.read_text(), re.MULTILINE)
                    if wrapper_match:
                        wrapper_target = wrapper_match.group(1)
                        local_data["wrapper_target"] = wrapper_target
                        local_wrapper_matches = resolve_path(wrapper_target) == resolve_path(mise_path)
                        local_data["matches_mise"] = local_wrapper_matches
                except OSError:
                    pass
        result["local_entries"][command] = local_data
        is_active_local_wrapper = Path(command_path) == local_entry and local_wrapper_matches
        if resolve_path(command_path) != resolve_path(mise_path) and not is_active_local_wrapper:
            result["status"] = "drift"
            result["issues"].append(
                f"{command} resolves to {resolve_path(command_path)}, expected {resolve_path(mise_path)} from mise"
            )

    exec_path, exec_error = command_output([node_path, "-p", "process.execPath"])
    result["node_exec_path"] = exec_path
    if not exec_path or resolve_path(exec_path) != resolve_path(node_path):
        result["status"] = "drift"
        result["issues"].append(
            f"node process.execPath is {exec_path or exec_error or 'unknown'}, expected {resolve_path(node_path)}"
        )
    prefix, prefix_error = command_output([npm_path, "config", "get", "prefix"])
    result["npm_prefix"] = prefix
    expected_prefix = str(Path(resolve_path(node_path)).parent.parent)
    result["expected_npm_prefix"] = expected_prefix
    if not prefix or resolve_path(prefix) != resolve_path(expected_prefix):
        result["status"] = "drift"
        result["issues"].append(
            f"npm prefix is {prefix or prefix_error or 'unknown'}, expected {expected_prefix} for active Node"
        )

    if result["status"] != "drift" and provider["id"] != "unsupported":
        result["status"] = "pass"
        result["executable"] = True
    return result


def load_catalog() -> list[dict[str, Any]]:
    data = json.loads(CATALOG_PATH.read_text())
    tools = data.get("tools")
    return tools if isinstance(tools, list) else []


def safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def tooling_class(record: dict[str, Any]) -> str:
    return record.get("tooling_class", "agent-cli")


def select_catalog_records(
    catalog: list[dict[str, Any]], selected: set[str], only_class: str | None
) -> list[dict[str, Any]]:
    return [
        record
        for record in catalog
        if (not selected or record.get("id") in selected)
        and (not only_class or tooling_class(record) == only_class)
    ]


def first_existing_command(commands: list[str]) -> tuple[str, str] | None:
    for command in commands:
        path = shutil.which(command)
        if path:
            return command, path
    return None


def resolve_path(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def detect_channel(path: str, resolved_path: str) -> str:
    joined = " ".join([path, resolved_path])
    if path.startswith(str(Path.home() / ".local/bin")) and "/Applications/" in resolved_path and ".app/" in resolved_path:
        return "script"
    if "/Applications/" in joined and ".app/" in joined:
        return "app-bundle"
    if "/opt/homebrew/" in joined or "/usr/local/Cellar/" in joined or "/opt/homebrew/Cellar/" in joined:
        return "brew"
    if "node_modules" in joined:
        return "npm"
    if "/.amp/" in joined:
        return "script"
    if "/.local/share/devin/cli/" in joined:
        return "desktop-install"
    if "/.local/bin/" in path and "/.local/share/" in resolved_path:
        return "script"
    return "unknown"


def binary_container(path: str, resolved_path: str) -> str:
    joined = " ".join([path, resolved_path])
    if "/Applications/" in joined and ".app/" in joined:
        return "app-bundle"
    return "standalone"


def app_bundle_version(resolved_path: str) -> str | None:
    """Return an app bundle's version when its CLI lacks a version command."""
    path = Path(resolved_path)
    for parent in [path, *path.parents]:
        if parent.suffix != ".app":
            continue
        info = parent / "Contents/Info.plist"
        output, _ = command_output(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(info)])
        return parse_version(output or "") or output
    return None


def source_checkout_details(path: str, source: dict[str, Any]) -> dict[str, Any]:
    """Read linked-checkout metadata without fetching or modifying Git state."""
    pattern = source.get("launcher_pattern")
    if pattern:
        try:
            launcher = Path(path).read_text()
        except OSError:
            return {}
        match = re.search(str(pattern), launcher)
        if not match:
            return {}
        target = Path(match.group(1))
        checkout = (Path(path).parent / target).resolve() if source.get("mode") == "launcher-relative" else target
    elif source.get("mode") == "resolved-parent":
        resolved = Path(resolve_path(path))
        checkout = next((parent for parent in [resolved.parent, *resolved.parents] if (parent / ".git").exists()), None)
        if checkout is None:
            return {}
    else:
        return {}
    if not (checkout / ".git").exists():
        return {"source_checkout_path": str(checkout), "source_checkout_error": "not a Git checkout"}

    def git_output(*args: str) -> str | None:
        output, _ = command_output(["git", "-C", str(checkout), *args])
        return output

    details: dict[str, Any] = {"source_checkout_path": str(checkout)}
    details["source_commit"] = git_output("rev-parse", "HEAD")
    details["source_dirty"] = bool(git_output("status", "--porcelain"))
    remote = str(source.get("upstream_remote", "upstream"))
    branch = str(source.get("upstream_branch", "master"))
    upstream_ref = f"{remote}/{branch}"
    details["source_upstream_ref"] = upstream_ref
    upstream_commit = git_output("rev-parse", upstream_ref)
    if not upstream_commit:
        details["source_upstream_error"] = f"{upstream_ref} is not available locally; run git fetch {remote} manually"
        return details
    details["source_upstream_commit"] = upstream_commit
    distance = git_output("rev-list", "--left-right", "--count", f"HEAD...{upstream_ref}")
    if distance:
        try:
            ahead, behind = distance.split()
            details["source_ahead"] = int(ahead)
            details["source_behind"] = int(behind)
        except ValueError:
            details["source_upstream_error"] = f"could not parse Git distance: {distance}"
    return details


def private_harness_entrypoint(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    current, _ = get_current_version(command, [["--version"], ["version"]]) if path else (None, None)
    return {
        "command": command,
        "path": path,
        "resolved_path": resolve_path(path) if path else None,
        "version": current,
        "evidence": "current-shell-command-resolution",
        "confidence": "high" if path else "none",
    }


def app_bundle_metadata(bundle: Path) -> dict[str, Any]:
    info = bundle / "Contents/Info.plist"
    version, _ = command_output(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(info)])
    identifier, _ = command_output(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier", str(info)])
    return {"bundle_path": str(bundle), "bundle_id": identifier, "bundle_version": version}


def configured_executables(path: Path, key: str) -> list[str]:
    """Extract only executable tokens from an explicit config key; never emit arguments."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    candidates = payload.get(key)
    if not isinstance(candidates, dict):
        return []
    executables: list[str] = []
    for value in candidates.values():
        if not isinstance(value, dict) or not isinstance(value.get("command"), str):
            continue
        try:
            executable = shlex.split(value["command"])[0]
        except ValueError:
            continue
        if executable:
            executables.append(executable)
    return sorted(set(executables))


def nori_local_agent_executables(path: Path) -> list[str]:
    """Read only `agents.distribution.local.command` values, excluding arguments and secrets."""
    try:
        text = path.read_text()
    except OSError:
        return []
    commands: list[str] = []
    in_local_distribution = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_local_distribution = stripped == "[agents.distribution.local]"
            continue
        if in_local_distribution and stripped.startswith("command"):
            match = re.match(r'command\s*=\s*["\']([^"\']+)', stripped)
            if match:
                commands.append(match.group(1))
    return sorted(set(commands))


def private_harness_inventory(baseline_path: Path | None = None) -> dict[str, Any]:
    """Inventory explicit host evidence without traversing application directories or reading secrets."""
    hosts: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    private_installations: list[dict[str, Any]] = []
    unknown: list[dict[str, str]] = []

    for host_id, name, bundle, config_path in PRIVATE_HARNESS_HOSTS:
        bundle_exists = bundle.is_dir()
        config_exists = config_path.exists()
        host = {
            "id": host_id,
            "name": name,
            "installed": bundle_exists,
            "evidence_paths": [str(bundle)] + ([str(config_path)] if config_exists else []),
            "evidence": "host-bundle-metadata" if bundle_exists else "not-found",
            "confidence": "high" if bundle_exists else "none",
        }
        if bundle_exists:
            host.update(app_bundle_metadata(bundle))
        hosts.append(host)
        bindings.append(
            {
                "client": host_id,
                "binding_status": "unconfirmed",
                "evidence_paths": [str(config_path)] if config_exists else [str(bundle)],
                "evidence": "host-installed; no explicit harness executable read from safe configuration",
                "confidence": "low" if bundle_exists else "none",
            }
        )
        for executable_name in ("codex", "agent", "node", "acpx", "nori"):
            candidate = bundle / "Contents/Resources" / executable_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                private_installations.append(
                    {
                        "host": host_id,
                        "path": str(candidate),
                        "resolved_path": resolve_path(str(candidate)),
                        # Do not execute host-private binaries during inventory. A host
                        # specific probe needs separate review before we can trust it.
                        "version": None,
                        "version_status": "not-probed",
                        "source": "known-host-install-directory",
                        "ownership_evidence": str(bundle),
                        "confidence": "high",
                    }
                )

    zed_config = Path.home() / ".config/zed/settings.json"
    zed_commands = configured_executables(zed_config, "agent_servers")
    if zed_commands:
        binding = next((item for item in bindings if item["client"] == "zed"), None)
        if binding:
            binding.update(
                {
                    "binding_status": "confirmed",
                    "executables": zed_commands,
                    "evidence_paths": [str(zed_config)],
                    "evidence": "explicit Zed agent_servers command configuration",
                    "confidence": "high",
                }
            )

    nori_config = Path.home() / ".nori/cli/config.toml"
    nori_commands = nori_local_agent_executables(nori_config)
    bindings.append(
        {
            "client": "nori",
            "binding_status": "confirmed" if nori_commands else "unconfirmed",
            "executables": nori_commands,
            "evidence_paths": [str(nori_config)] if nori_config.exists() else [],
            "evidence": "explicit Nori local-agent command configuration" if nori_commands else "no local-agent command configured",
            "confidence": "high" if nori_commands else "low",
        }
    )
    for client, command in (("acpx", "acpx"), ("codex-acp", "codex-acp")):
        bindings.append(
            {
                "client": client,
                "binding_status": "unconfirmed",
                "evidence_paths": [shutil.which(command)] if shutil.which(command) else [],
                "evidence": "client executable found, but no safe local harness binding configuration is known",
                "confidence": "low" if shutil.which(command) else "none",
            }
        )

    for host in hosts:
        if host["installed"] and not any(entry.get("host") == host["id"] for entry in private_installations):
            unknown.append(
                {
                    "subject": host["id"],
                    "reason": "host is installed, but no private harness was found in the limited known paths",
                }
            )

    result: dict[str, Any] = {
        "inventory_version": 1,
        "scope": "read-only explicit host metadata, known paths, and safe binding keys; no recursive app scan or secret output",
        "default_entrypoints": [private_harness_entrypoint(command) for command in DEFAULT_HARNESS_ENTRYPOINTS],
        "private_installations": private_installations,
        "hosts": hosts,
        "client_harness_bindings": bindings,
        "unknown": unknown,
        # A governance risk is only meaningful once a private binary is both
        # discovered and proven to be the client's active binding. This limited
        # inventory intentionally does not infer risks from host presence alone.
        "governance_risks": [],
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"))
    result["fingerprint"] = hashlib.sha256(canonical.encode()).hexdigest()
    if baseline_path:
        try:
            baseline = safe_dict(json.loads(baseline_path.read_text()))
            baseline = safe_dict(baseline.get("private_harness_inventory", baseline))
            baseline_fingerprint = baseline.get("fingerprint")
            result["baseline"] = {
                "path": str(baseline_path),
                "status": "unchanged" if baseline_fingerprint == result["fingerprint"] else "changed",
                "action": "review this explicit inventory manually" if baseline_fingerprint != result["fingerprint"] else None,
            }
        except (OSError, json.JSONDecodeError):
            result["baseline"] = {"path": str(baseline_path), "status": "unavailable", "action": "review manually"}
    return result


def classify_brew_channel(brew_info: dict[str, Any] | None) -> str:
    brew_info = safe_dict(brew_info)
    if not brew_info:
        return "brew"
    tap = brew_info.get("tap")
    kind = brew_info.get("kind")
    if kind == "cask":
        return "brew-cask"
    if tap == "homebrew/core":
        return "brew-core"
    if tap and tap != "homebrew/core":
        return "brew-tap"
    return "brew"


def parse_version(text: str) -> str | None:
    match = SEMVER_RE.search(text)
    return match.group(1) if match else None


def version_key(version: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$", version)
    if not match:
        return None
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    suffix = match.group(4)
    stable_rank = 1 if not suffix else 0
    return (major, minor, patch, stable_rank)


def get_current_version(command: str, version_args: list[list[str]]) -> tuple[str | None, str | None]:
    for argv in version_args:
        try:
            completed = run([command, *argv], timeout=15)
        except Exception as exc:
            last_error = str(exc)
            continue
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
        version = parse_version(output)
        if version or output:
            return version, output
    return None, last_error if "last_error" in locals() else None


def get_brew_info(package: str, warnings: list[str] | None = None) -> dict[str, Any] | None:
    if not shutil.which("brew"):
        return None
    try:
        completed = run(
            ["brew", "info", "--json=v2", package],
            env={"HOMEBREW_NO_AUTO_UPDATE": "1"},
            timeout=PACKAGE_MANAGER_TIMEOUT_SECONDS,
            use_system_proxy=True,
        )
    except subprocess.TimeoutExpired:
        if warnings is not None:
            warnings.append(f"brew info {package} timed out after {PACKAGE_MANAGER_TIMEOUT_SECONDS}s")
        return None
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"brew info {package} failed: {exc}")
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

    formulae = safe_list(payload.get("formulae"))
    if formulae:
        item = formulae[0]
        item["kind"] = "formula"
        return item

    casks = safe_list(payload.get("casks"))
    if casks:
        item = casks[0]
        item["kind"] = "cask"
        return item
    return None


def brew_info_is_installed(brew_info: dict[str, Any] | None) -> bool:
    brew_info = safe_dict(brew_info)
    if not brew_info:
        return False
    if brew_info.get("kind") == "formula":
        return bool(brew_info.get("installed"))
    if brew_info.get("kind") == "cask":
        return brew_info.get("installed") is not None
    return False


def get_npm_latest(package: str, warnings: list[str] | None = None) -> str | None:
    try:
        completed = run(
            ["npm", "view", package, "version"],
            timeout=PACKAGE_MANAGER_TIMEOUT_SECONDS,
            use_system_proxy=True,
        )
    except subprocess.TimeoutExpired:
        if warnings is not None:
            warnings.append(f"npm view {package} timed out after {PACKAGE_MANAGER_TIMEOUT_SECONDS}s")
        return None
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"npm view {package} failed: {exc}")
        return None
    if completed.returncode != 0:
        if warnings is not None:
            message = completed.stderr.strip() or completed.stdout.strip() or "non-zero exit"
            warnings.append(f"npm view {package} failed: {message}")
        return None
    version = completed.stdout.strip()
    return version or None


def http_get_json(url: str, warnings: list[str] | None = None) -> dict[str, Any] | None:
    last_error = "request failed"
    for _ in range(HTTP_ATTEMPTS):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json, application/json",
                "User-Agent": "agent-cli-audit",
            },
        )
        try:
            # urllib's default opener honors HTTP(S)_PROXY and macOS system proxy settings.
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                # Upstream endpoints occasionally prepend malformed bytes. Keep
                # the JSON lookup item-scoped instead of failing every dependent CLI.
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue
    if warnings is not None:
        warnings.append(f"GET {url} failed after {HTTP_ATTEMPTS} attempt(s): {last_error}")
    return None


def http_get_text(url: str, warnings: list[str] | None = None) -> str | None:
    last_error = "request failed"
    for _ in range(HTTP_ATTEMPTS):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/plain, text/html, application/json",
                "User-Agent": "agent-cli-audit",
            },
        )
        try:
            # urllib's default opener honors HTTP(S)_PROXY and macOS system proxy settings.
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = str(exc)
            continue
    if warnings is not None:
        warnings.append(f"GET {url} failed after {HTTP_ATTEMPTS} attempt(s): {last_error}")
    return None


def get_nested_field(payload: dict[str, Any], field: str) -> Any:
    current: Any = payload
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def release_platform() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch_map = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }
    arch = arch_map.get(machine)
    if system not in {"darwin", "linux"} or not arch:
        return None
    return f"{system}_{arch}"


def get_latest_from_source(source: dict[str, Any], warnings: list[str] | None = None) -> str | None:
    source = safe_dict(source)
    source_type = source.get("type")
    url = source.get("url")
    url_template = source.get("url_template")
    if url_template:
        target = release_platform()
        if not target:
            if warnings is not None:
                warnings.append("Could not determine a supported platform for the latest-version source")
            return None
        url = str(url_template).format(platform=target)
    if not source_type or not url:
        return None
    if source_type == "text":
        text = http_get_text(url, warnings)
        if not text:
            return None
        return text.strip().splitlines()[0].strip() or None
    if source_type == "regex":
        text = http_get_text(url, warnings)
        if not text:
            return None
        pattern = source.get("pattern")
        if not pattern:
            return None
        match = re.search(pattern, text)
        return match.group(1) if match else None
    if source_type == "json":
        payload = http_get_json(url, warnings)
        if not payload:
            return None
        field = source.get("field")
        if not field:
            return None
        value = get_nested_field(payload, field)
        return str(value) if value is not None else None
    return None


def parse_github_repo_from_releases(url: str) -> tuple[str, str] | None:
    match = GITHUB_RELEASES_RE.match(url.rstrip("/"))
    if not match:
        return None
    return match.group(1), match.group(2)


def summarize_release_notes(payload: dict[str, Any]) -> dict[str, Any]:
    payload = safe_dict(payload)
    body = (payload.get("body") or "").strip()
    name = payload.get("name") or payload.get("tag_name") or ""
    published = payload.get("published_at")
    html_url = payload.get("html_url")
    combined = f"{name}\n{body}".lower()
    risk_hits = [term for term in HIGH_RISK_TERMS if term in combined]
    medium_hits = [term for term in MEDIUM_RISK_TERMS if term in combined]
    if risk_hits:
        risk = "high"
    elif medium_hits:
        risk = "medium"
    else:
        risk = "low"

    bullets: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("* ", "- ")):
            bullets.append(stripped[2:].strip())
        elif re.match(r"^\d+\.\s+", stripped):
            bullets.append(re.sub(r"^\d+\.\s+", "", stripped))
        if len(bullets) >= 5:
            break

    return {
        "version": payload.get("tag_name") or name,
        "published_at": published,
        "url": html_url,
        "risk_level": risk,
        "risk_terms": sorted(set(risk_hits + medium_hits)),
        "highlights": bullets,
    }


def strip_html(raw: str) -> str:
    text = HTML_TAG_RE.sub(" ", raw)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def summarize_text_release(version: str, text: str, url: str, note: str | None = None) -> dict[str, Any]:
    combined = text.lower()
    risk_hits = [term for term in HIGH_RISK_TERMS if term in combined]
    medium_hits = [term for term in MEDIUM_RISK_TERMS if term in combined]
    if risk_hits:
        risk = "high"
    elif medium_hits:
        risk = "medium"
    else:
        risk = "low"

    fragments = re.split(r"(?<=[.!?])\s+", text)
    highlights: list[str] = []
    for fragment in fragments:
        snippet = fragment.strip(" -")
        if len(snippet) < 20:
            continue
        highlights.append(snippet[:220])
        if len(highlights) >= 3:
            break

    result = {
        "version": version,
        "url": url,
        "risk_level": risk,
        "risk_terms": sorted(set(risk_hits + medium_hits)),
        "highlights": highlights,
    }
    if note:
        result["note"] = note
    return result


def get_custom_release_summary(config: dict[str, Any], latest_version: str | None) -> dict[str, Any] | None:
    config = safe_dict(config)
    source_type = config.get("type")
    url = config.get("url")
    if not source_type or not url:
        return None

    raw = http_get_text(url)
    if not raw:
        return None
    text = strip_html(raw)
    if not text:
        return None

    if source_type == "html-version-match":
        if not latest_version:
            return None
        version_pattern = re.escape(latest_version)
        match = re.search(version_pattern, text)
        note = None
        if not match and config.get("fallback_latest"):
            match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b", text)
            note = "Changelog page did not contain an exact match for the installer latest version."
        if not match:
            return None
        start = max(0, match.start() - 240)
        end = min(len(text), match.end() + 480)
        return summarize_text_release(latest_version, text[start:end], url, note)

    if source_type == "html-first-version":
        prefix = config.get("version_prefix", "")
        pattern = r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b"
        if prefix:
            pattern = re.escape(prefix) + r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b"
        match = re.search(pattern, text)
        if not match:
            return None
        found_version = match.group(0)
        if prefix and found_version.startswith(prefix):
            found_version = found_version[len(prefix):]
        note = None
        if latest_version and found_version != latest_version:
            note = (
                f"Changelog page headline version is {prefix}{found_version}, "
                f"but installer latest is {latest_version}."
            )
        start = max(0, match.start() - 240)
        end = min(len(text), match.end() + 480)
        return summarize_text_release(latest_version or found_version, text[start:end], url, note)

    return None


def get_release_summary(release_notes_url: str) -> dict[str, Any] | None:
    repo = parse_github_repo_from_releases(release_notes_url)
    if not repo:
        return None
    owner, name = repo
    payload = http_get_json(f"https://api.github.com/repos/{owner}/{name}/releases/latest")
    if not isinstance(payload, dict) or not payload:
        return None
    return summarize_release_notes(payload)


def channel_update_command(record: dict[str, Any], normalized_channel: str) -> str | None:
    tool = record["id"]
    if normalized_channel in {"brew", "brew-core", "brew-tap", "brew-cask"} and record.get("brew_package"):
        package = record["brew_package"]
        if normalized_channel == "brew-cask":
            return f"brew upgrade --cask {package}"
        return f"brew upgrade {package}"
    if normalized_channel == "npm" and record.get("npm_package"):
        prefix = npm_command_prefix()
        if prefix:
            return f"{prefix} install -g {record['npm_package']}@latest"
        return None
    if normalized_channel == "script":
        if tool == "kiro-cli":
            return "curl -fsSL https://cli.kiro.dev/install | bash"
        if tool == "uv":
            return "curl -LsSf https://astral.sh/uv/install.sh | sh"
    return None


def native_update_command(tool: str, normalized_channel: str) -> str | None:
    method_map = {
        "brew": "brew",
        "brew-core": "brew",
        "brew-tap": "brew",
        "brew-cask": "brew",
        "github-release": "curl",
        "script": "curl",
        "npm": "npm",
    }
    if tool in {"kilocode", "opencode"}:
        method = method_map.get(normalized_channel)
        if method:
            binary = "kilo" if tool == "kilocode" else "opencode"
            command = f"{binary} upgrade --method {method}"
            if normalized_channel == "npm":
                prefix = node_execution_prefix()
                return f"{prefix} {command}" if prefix else None
            return command
        return None
    commands = {
        "amp": "amp update",
        "claude": "claude update",
        "copilot": "copilot update",
        "cursor-agent": "agent update",
        "devin": "devin update",
        "droid": "droid update",
        "fx": "fx upgrade",
        "grok": "grok update",
        "hermes": "hermes update",
    }
    return commands.get(tool)


def upgrade_guidance(
    record: dict[str, Any], normalized_channel: str, status: str, migration_command: str | None
) -> dict[str, Any]:
    tool = record["id"]
    channel_command = channel_update_command(record, normalized_channel)
    native_command = native_update_command(tool, normalized_channel)
    alternatives: list[dict[str, str]] = []

    if tool == "codex" and normalized_channel == "app-bundle":
        return {
            "title": "Update ChatGPT.app",
            "kind": "app_bundle_update",
            "command": None,
            "reason": "This Codex binary is bundled with ChatGPT.app rather than a standalone CLI install.",
            "channel_effect": "Keeps the app-bundled installation managed by ChatGPT.",
            "alternatives": alternatives,
        }

    if tool == "antigravity" and normalized_channel == "script":
        return {
            "title": "Allow Antigravity's background self-updater to run",
            "kind": "background_self_update",
            "command": None,
            "reason": "The official installer delegates routine updates to the CLI's background self-updater during normal runs.",
            "channel_effect": "Preserves the official script installation.",
            "alternatives": alternatives,
        }

    if status == "nonstandard" and migration_command:
        return {
            "title": "Migrate to the vendor-recommended channel",
            "kind": "migration",
            "command": migration_command,
            "reason": "The current install channel is no longer a supported routine-upgrade path.",
            "channel_effect": "Changes the installation channel.",
            "alternatives": alternatives,
        }

    if tool in {"kilocode", "opencode"} and native_command:
        return {
            "title": f"Preserve {normalized_channel} ownership",
            "kind": "native_channel_update",
            "command": native_command,
            "reason": "The CLI accepts an explicit installation method matching the detected channel.",
            "channel_effect": "Preserves the detected installation method.",
            "alternatives": alternatives,
        }

    if normalized_channel in {"npm", "brew", "brew-core", "brew-tap", "brew-cask"} and channel_command:
        if native_command:
            alternatives.append(
                {
                    "title": "Vendor native self-update",
                    "command": native_command,
                    "reason": "Available from the CLI, but its effect on package-manager ownership is not verified.",
                    "channel_effect": "May replace the managed binary outside the current package manager.",
                }
            )
        return {
            "title": f"Preserve {normalized_channel} ownership",
            "kind": "channel_update",
            "command": channel_command,
            "reason": "The current install is managed by this supported package channel.",
            "channel_effect": "Preserves package-manager ownership.",
            "alternatives": alternatives,
        }

    if tool == "codex" and normalized_channel == "script":
        return {
            "title": "Run the official standalone installer",
            "kind": "official_installer",
            "command": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
            "reason": "OpenAI documents the standalone installer as the update path for script installs.",
            "channel_effect": "Refreshes the standalone CLI installation.",
            "alternatives": alternatives,
        }

    if native_command:
        return {
            "title": "Run the vendor native self-update",
            "kind": "native_self_update",
            "command": native_command,
            "reason": "The current installation is a vendor-managed direct or script path.",
            "channel_effect": "Expected to update the direct install; package-manager ownership is not applicable.",
            "alternatives": alternatives,
        }

    if channel_command:
        return {
            "title": "Upgrade through the current channel",
            "kind": "channel_update",
            "command": channel_command,
            "reason": "This is the available routine upgrade path for the detected channel.",
            "channel_effect": "Preserves the detected installation channel.",
            "alternatives": alternatives,
        }

    return {
        "title": "Review official installation guidance",
        "kind": "manual_review",
        "command": None,
        "reason": "No safe routine upgrade command is verified for this installation.",
        "channel_effect": "No command is proposed.",
        "alternatives": alternatives,
    }


def get_update_command(record: dict[str, Any], normalized_channel: str) -> str:
    guidance = upgrade_guidance(
        record,
        normalized_channel,
        channel_status(record, normalized_channel),
        None,
    )
    return guidance.get("command") or "See official install docs"


def get_migration_command(record: dict[str, Any]) -> str | None:
    tool = record["id"]
    if tool == "kiro-cli":
        return "brew uninstall --cask kiro-cli && curl -fsSL https://cli.kiro.dev/install | bash"
    if tool == "codex":
        prefix = npm_command_prefix()
        return f"{prefix} uninstall -g @openai/codex && curl -fsSL https://chatgpt.com/codex/install.sh | sh" if prefix else None
    if tool == "claude":
        return "brew uninstall --cask claude-code && curl -fsSL https://claude.ai/install.sh | bash"
    if tool == "amp":
        prefix = npm_command_prefix()
        return f"{prefix} uninstall -g @ampcode/cli && curl -fsSL https://ampcode.com/install.sh | bash" if prefix else None
    if tool == "droid":
        prefix = npm_command_prefix()
        return f"{prefix} uninstall -g droid && curl -fsSL https://app.factory.ai/cli | sh" if prefix else None
    if tool == "uv":
        return "brew uninstall uv && curl -LsSf https://astral.sh/uv/install.sh | sh"
    return None


def get_migration_target(record: dict[str, Any]) -> str | None:
    channels = safe_list(record.get("recommended_channels"))
    if not channels:
        return None
    return channels[0]


def is_upgrade_candidate(row: dict[str, Any]) -> bool:
    current = row.get("current_version")
    latest = row.get("latest_version")
    if not current or not latest:
        return False
    current_parsed = version_key(current)
    latest_parsed = version_key(latest)
    if current_parsed is None or latest_parsed is None:
        if current == latest:
            return False
    elif latest_parsed <= current_parsed:
        return False
    return row.get("channel_status") in {"recommended", "supported"} and row.get("update_command") != "See official install docs"


def channel_status(record: dict[str, Any], normalized_channel: str) -> str:
    recommended_channels = safe_list(record.get("recommended_channels"))
    supported_channels = safe_list(record.get("supported_channels"))
    if normalized_channel in recommended_channels:
        return "recommended"
    if normalized_channel in supported_channels:
        return "supported"
    family_aliases = {
        "brew-core": "brew",
        "brew-tap": "brew",
        "brew-cask": "brew",
    }
    family = family_aliases.get(normalized_channel)
    if family and family in supported_channels:
        return "supported"
    if normalized_channel == "brew" and "brew" in supported_channels:
        return "supported"
    return "nonstandard"


def build_result(record: dict[str, Any], online: bool, with_release_notes: bool) -> dict[str, Any] | None:
    installed = first_existing_command(record["commands"])
    if not installed:
        return None

    command, path = installed
    resolved = resolve_path(path)
    detected_channel = detect_channel(path, resolved)
    container = binary_container(path, resolved)
    source_details = source_checkout_details(path, safe_dict(record.get("source_checkout")))
    if source_details.get("source_checkout_path"):
        detected_channel = "source"
    elif detected_channel == "app-bundle" and record.get("app_bundle_channel"):
        detected_channel = str(record["app_bundle_channel"])
    elif detected_channel == "unknown" and record.get("path_channel"):
        detected_channel = str(record["path_channel"])
    if detected_channel == "unknown" and path.startswith(str(Path.home() / ".local/bin")):
        detected_channel = "script"

    brew_info: dict[str, Any] | None = None
    normalized_channel = detected_channel
    latest_version = None
    extra: dict[str, Any] = {}
    warnings: list[str] = []

    if detected_channel in {"brew", "app-bundle", "script"} and record.get("brew_package"):
        brew_info = get_brew_info(record["brew_package"], warnings)
        if brew_info and (detected_channel == "brew" or brew_info_is_installed(brew_info)):
            brew_info = safe_dict(brew_info)
            detected_channel = "brew"
            normalized_channel = classify_brew_channel(brew_info)
            extra["brew_tap"] = brew_info.get("tap")
            if brew_info.get("kind") == "formula":
                latest_version = safe_dict(brew_info.get("versions")).get("stable")
            elif brew_info.get("kind") == "cask":
                latest_version = brew_info.get("version")

    if online and record.get("latest_source"):
        source_latest = get_latest_from_source(record["latest_source"], warnings)
        if source_latest:
            extra["source_latest"] = source_latest
            if latest_version is None or normalized_channel in {"script", "app-bundle", "unknown"}:
                latest_version = source_latest

    if online and record["id"] in {"codex", "gemini", "opencode", "kilocode", "droid", "copilot"} and record.get("npm_package"):
        npm_latest = get_npm_latest(record["npm_package"], warnings)
        if npm_latest:
            extra["npm_latest"] = npm_latest
            if normalized_channel == "npm":
                latest_version = npm_latest
    elif online and latest_version is None and record.get("npm_package") and normalized_channel == "npm":
        latest_version = get_npm_latest(record["npm_package"], warnings)

    current_version, version_raw = get_current_version(command, record["version_args"])
    if current_version is None and container == "app-bundle":
        bundle_version = app_bundle_version(resolved)
        if bundle_version:
            current_version = bundle_version
            version_raw = f"Bundle version: {bundle_version}"

    channel_notes = safe_dict(record.get("channel_notes"))
    notes = channel_notes.get(normalized_channel)
    if not notes and normalized_channel == "brew" and channel_notes.get("brew"):
        notes = channel_notes["brew"]

    status = channel_status(record, normalized_channel)
    migration_target = None
    migration_command = None
    if status != "recommended" and not (record["id"] == "codex" and normalized_channel == "app-bundle"):
        migration_target = get_migration_target(record)
        migration_command = get_migration_command(record)

    guidance = upgrade_guidance(record, normalized_channel, status, migration_command)
    result = {
        "id": record["id"],
        "name": record["name"],
        "tooling_class": tooling_class(record),
        "command": command,
        "path": path,
        "resolved_path": resolved,
        "current_version": current_version,
        "latest_version": latest_version,
        "version_raw": version_raw,
        "detected_channel": detected_channel,
        "normalized_channel": normalized_channel,
        "binary_container": container,
        "channel_status": status,
        "official_install_url": record["official_install_url"],
        "official_release_notes_url": record["official_release_notes_url"],
        "update_command": guidance.get("command") or "See official install docs",
        "upgrade_guidance": guidance,
        "upgrade_candidate": False,
        "migration_target": migration_target,
        "migration_command": migration_command,
        "notes": notes,
    }
    if online and with_release_notes:
        release_summary = get_release_summary(record["official_release_notes_url"])
        if not release_summary and record.get("custom_release_notes"):
            release_summary = get_custom_release_summary(record["custom_release_notes"], latest_version)
        if release_summary:
            result["release_summary"] = release_summary
    result["upgrade_candidate"] = is_upgrade_candidate(result)
    result.update(extra)
    result.update(source_details)
    if warnings:
        result["audit_warnings"] = warnings
    return result


def filter_rows(
    rows: list[dict[str, Any]],
    only_outdated: bool,
    only_nonstandard: bool,
    only_class: str | None,
) -> list[dict[str, Any]]:
    filtered = rows
    if only_outdated:
        filtered = [row for row in filtered if row.get("upgrade_candidate")]
    if only_nonstandard:
        filtered = [row for row in filtered if row.get("channel_status") == "nonstandard"]
    if only_class:
        filtered = [row for row in filtered if row.get("tooling_class", "agent-cli") == only_class]
    return filtered


def render_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "tool",
        "class",
        "command",
        "current",
        "latest",
        "channel",
        "status",
    ]
    data = [
        [
            row["id"],
            row.get("tooling_class", "agent-cli"),
            row["command"],
            row.get("current_version") or "?",
            row.get("latest_version") or row.get("npm_latest") or "?",
            row["normalized_channel"],
            row["channel_status"],
        ]
        for row in rows
    ]
    widths = [len(header) for header in headers]
    for line in data:
        widths = [max(width, len(cell)) for width, cell in zip(widths, line)]

    def fmt(line: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(line, widths))

    output = [fmt(headers), fmt(["-" * width for width in widths])]
    output.extend(fmt(line) for line in data)
    return "\n".join(output)


def render_detail(row: dict[str, Any]) -> str:
    lines = [
        f"[{row['id']}] {row['name']}",
        f"  class: {row.get('tooling_class', 'agent-cli')}",
        f"  command: {row['command']}",
        f"  path: {row['path']}",
        f"  resolved_path: {row['resolved_path']}",
        f"  current_version: {row.get('current_version') or 'unknown'}",
        f"  latest_version: {row.get('latest_version') or row.get('npm_latest') or 'unknown'}",
        f"  install_channel: {row['normalized_channel']}",
        f"  official_status: {row['channel_status']}",
        f"  update_command: {row['update_command']}",
        f"  install_docs: {row['official_install_url']}",
        f"  release_notes: {row['official_release_notes_url']}",
    ]
    if row.get("migration_target"):
        lines.append(f"  migration_target: {row['migration_target']}")
    if row.get("migration_command"):
        lines.append(f"  migration_command: {row['migration_command']}")
    if row.get("brew_tap"):
        lines.append(f"  brew_tap: {row['brew_tap']}")
    if row.get("npm_latest"):
        lines.append(f"  npm_latest: {row['npm_latest']}")
    if row.get("source_latest"):
        lines.append(f"  source_latest: {row['source_latest']}")
    if row.get("notes"):
        lines.append(f"  notes: {row['notes']}")
    if row.get("release_summary"):
        summary = row["release_summary"]
        lines.append(f"  release_risk: {summary.get('risk_level', 'unknown')}")
        if summary.get("note"):
            lines.append(f"  release_note: {summary['note']}")
        if summary.get("published_at"):
            lines.append(f"  release_published_at: {summary['published_at']}")
        if summary.get("risk_terms"):
            lines.append(f"  release_risk_terms: {', '.join(summary['risk_terms'])}")
        for highlight in summary.get("highlights", [])[:3]:
            lines.append(f"  release_highlight: {highlight}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit installed agent CLIs on this machine.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--all", action="store_true", help="Show all catalog entries, including missing ones.")
    parser.add_argument("--offline", action="store_true", help="Skip network-backed latest version checks.")
    parser.add_argument("--check-node-runtime", action="store_true", help="Run the read-only mise Node runtime drift check instead of the catalog audit.")
    parser.add_argument("--inventory-private-harnesses", action="store_true", help="Run a read-only inventory of explicit private Agent Harness evidence instead of the catalog audit.")
    parser.add_argument("--private-harness-baseline", type=Path, help="Optional external JSON inventory baseline to compare read-only; this tool never writes it.")
    parser.add_argument("--tool", action="append", dest="tools", help="Only audit this tool id. Repeatable.")
    parser.add_argument("--with-release-notes", action="store_true", help="Fetch latest GitHub release notes and risk summary where possible.")
    parser.add_argument("--only-outdated", action="store_true", help="Only show installed tools that are upgrade candidates.")
    parser.add_argument("--only-nonstandard", action="store_true", help="Only show installed tools on nonstandard install channels.")
    parser.add_argument("--only-class", choices=["agent-cli", "tooling-runtime", "agent-operations"], help="Only show entries from a specific tooling class.")
    args = parser.parse_args()

    if args.check_node_runtime:
        runtime_drift = check_node_runtime()
        if args.json:
            print(json.dumps({"runtime_drift": runtime_drift}, indent=2, ensure_ascii=True))
        else:
            print(f"Node runtime drift check: {runtime_drift['status']}")
            for issue in runtime_drift["issues"]:
                print(f"- {issue}")
            print(runtime_drift["gui_probe_required"])
        return 0 if runtime_drift["status"] == "pass" else 1

    if args.inventory_private_harnesses:
        inventory = private_harness_inventory(args.private_harness_baseline)
        if args.json:
            print(json.dumps({"private_harness_inventory": inventory}, indent=2, ensure_ascii=True))
        else:
            print("Private Agent Harness inventory (read-only)")
            for entry in inventory["default_entrypoints"]:
                print(f"- default {entry['command']}: {entry['resolved_path'] or 'not found'}")
            print(f"- private installations: {len(inventory['private_installations'])}")
            for binding in inventory["client_harness_bindings"]:
                print(f"- {binding['client']}: {binding['binding_status']} ({binding['confidence']})")
            if inventory["unknown"]:
                print(f"- unconfirmed hosts: {', '.join(item['subject'] for item in inventory['unknown'])}")
            if inventory.get("baseline", {}).get("status") == "changed":
                print("- baseline: changed; review this explicit inventory manually")
        return 0

    catalog = load_catalog()
    catalog_revision = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()[:12]
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    selected = set(args.tools or [])
    records = select_catalog_records(catalog, selected, args.only_class)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(AUDIT_WORKERS, len(records) or 1)) as executor:
        futures = {
            executor.submit(build_result, record, online=not args.offline, with_release_notes=args.with_release_notes): record
            for record in records
        }
        for future in concurrent.futures.as_completed(futures):
            record = futures[future]
            try:
                item = future.result()
            except Exception as exc:
                errors.append({
                    "id": record.get("id", "unknown"),
                    "name": record.get("name", "unknown"),
                    "error": str(exc),
                })
                continue
            if item is None:
                missing.append({
                    "id": record["id"],
                    "name": record["name"],
                    "commands": record["commands"],
                })
                continue
            for warning in safe_list(item.get("audit_warnings")):
                errors.append({
                    "id": record["id"],
                    "name": record["name"],
                    "error": str(warning),
                })
            rows.append(item)

    rows.sort(key=lambda row: row["id"])
    errors.sort(key=lambda item: (item["id"], item["error"]))
    rows = filter_rows(rows, args.only_outdated, args.only_nonstandard, None)

    if args.json:
        payload: dict[str, Any] = {
            "installed": rows,
            "audit_metadata": {
                "catalog_entries": len(catalog),
                "selected_catalog_entries": len(records),
                "catalog_revision": catalog_revision,
            },
        }
        if args.all:
            payload["missing"] = missing
        if errors:
            payload["errors"] = errors
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0

    print(render_table(rows))
    print()
    for row in rows:
        print(render_detail(row))
        print()

    if args.all and missing:
        print("Missing catalog entries:")
        for item in missing:
            commands = ", ".join(item["commands"])
            print(f"  - {item['id']}: not found in PATH (checked: {commands})")

    if errors:
        print()
        print("Audit warnings:")
        for item in errors:
            print(f"  - {item['id']}: {item['error']}")

    topgrade = shutil.which("topgrade")
    if topgrade:
        print()
        print("topgrade:")
        print(f"  installed at {topgrade}")
        print("  Use it as an execution engine for bulk upgrades, but keep this audit for CLI-specific policy checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
