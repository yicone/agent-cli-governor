#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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
SEMVER_RE = re.compile(r"\b\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b")
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
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        text=True,
        capture_output=True,
        env=merged_env,
        timeout=timeout,
        check=check,
    )


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
    return match.group(0) if match else None


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


def get_brew_info(package: str) -> dict[str, Any] | None:
    try:
        completed = run(
            ["brew", "info", "--json=v2", package],
            env={"HOMEBREW_NO_AUTO_UPDATE": "1"},
            timeout=30,
        )
    except Exception:
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


def get_npm_latest(package: str) -> str | None:
    try:
        completed = run(["npm", "view", package, "version"], timeout=20)
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    version = completed.stdout.strip()
    return version or None


def http_get_json(url: str) -> dict[str, Any] | None:
    for _ in range(3):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json, application/json",
                "User-Agent": "agent-cli-audit",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            continue
    return None


def http_get_text(url: str) -> str | None:
    for _ in range(3):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/plain, text/html, application/json",
                "User-Agent": "agent-cli-audit",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            continue
    return None


def get_nested_field(payload: dict[str, Any], field: str) -> Any:
    current: Any = payload
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def get_latest_from_source(source: dict[str, Any]) -> str | None:
    source = safe_dict(source)
    source_type = source.get("type")
    url = source.get("url")
    if not source_type or not url:
        return None
    if source_type == "text":
        text = http_get_text(url)
        if not text:
            return None
        return text.strip().splitlines()[0].strip() or None
    if source_type == "regex":
        text = http_get_text(url)
        if not text:
            return None
        pattern = source.get("pattern")
        if not pattern:
            return None
        match = re.search(pattern, text)
        return match.group(1) if match else None
    if source_type == "json":
        payload = http_get_json(url)
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


def get_update_command(record: dict[str, Any], normalized_channel: str) -> str:
    tool = record["id"]
    if tool == "amp":
        return "amp update"
    if normalized_channel in {"brew", "brew-core", "brew-tap", "brew-cask"} and record.get("brew_package"):
        package = record["brew_package"]
        if normalized_channel == "brew-cask":
            return f"brew upgrade --cask {package}"
        return f"brew upgrade {package}"
    if normalized_channel == "npm" and record.get("npm_package"):
        return f"npm install -g {record['npm_package']}@latest"
    if normalized_channel == "script":
        if tool == "codex":
            return "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
        if tool == "claude":
            return "curl -fsSL https://claude.ai/install.sh | bash"
        if tool == "amp":
            return "amp update"
        if tool == "droid":
            return "curl -fsSL https://app.factory.ai/cli | sh"
        if tool == "kiro-cli":
            return "curl -fsSL https://cli.kiro.dev/install | bash"
        if tool == "uv":
            return "curl -LsSf https://astral.sh/uv/install.sh | sh"
    return "See official install docs"


def get_migration_command(record: dict[str, Any]) -> str | None:
    tool = record["id"]
    if tool == "kiro-cli":
        return "brew uninstall --cask kiro-cli && curl -fsSL https://cli.kiro.dev/install | bash"
    if tool == "codex":
        return "npm uninstall -g @openai/codex && curl -fsSL https://chatgpt.com/codex/install.sh | sh"
    if tool == "claude":
        return "brew uninstall --cask claude-code && curl -fsSL https://claude.ai/install.sh | bash"
    if tool == "amp":
        return "npm uninstall -g @ampcode/cli && curl -fsSL https://ampcode.com/install.sh | bash"
    if tool == "droid":
        return "npm uninstall -g droid && curl -fsSL https://app.factory.ai/cli | sh"
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
    if detected_channel == "unknown" and path.startswith(str(Path.home() / ".local/bin")):
        detected_channel = "script"

    brew_info: dict[str, Any] | None = None
    normalized_channel = detected_channel
    latest_version = None
    extra = {}

    if detected_channel in {"brew", "app-bundle", "script"} and record.get("brew_package"):
        brew_info = get_brew_info(record["brew_package"])
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
        source_latest = get_latest_from_source(record["latest_source"])
        if source_latest:
            extra["source_latest"] = source_latest
            if latest_version is None or normalized_channel in {"script", "app-bundle", "unknown"}:
                latest_version = source_latest

    if online and latest_version is None and record.get("npm_package") and normalized_channel == "npm":
        latest_version = get_npm_latest(record["npm_package"])

    if online and record["id"] in {"codex", "gemini", "opencode", "kilocode", "droid", "copilot"} and record.get("npm_package"):
        npm_latest = get_npm_latest(record["npm_package"])
        if npm_latest:
            extra["npm_latest"] = npm_latest
            if normalized_channel == "npm":
                latest_version = npm_latest

    current_version, version_raw = get_current_version(command, record["version_args"])

    channel_notes = safe_dict(record.get("channel_notes"))
    notes = channel_notes.get(normalized_channel)
    if not notes and normalized_channel == "brew" and channel_notes.get("brew"):
        notes = channel_notes["brew"]

    migration_target = None
    migration_command = None
    if channel_status(record, normalized_channel) != "recommended":
        migration_target = get_migration_target(record)
        migration_command = get_migration_command(record)

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
        "channel_status": channel_status(record, normalized_channel),
        "official_install_url": record["official_install_url"],
        "official_release_notes_url": record["official_release_notes_url"],
        "update_command": get_update_command(record, normalized_channel),
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
    parser.add_argument("--with-release-notes", action="store_true", help="Fetch latest GitHub release notes and risk summary where possible.")
    parser.add_argument("--only-outdated", action="store_true", help="Only show installed tools that are upgrade candidates.")
    parser.add_argument("--only-nonstandard", action="store_true", help="Only show installed tools on nonstandard install channels.")
    parser.add_argument("--only-class", choices=["agent-cli", "tooling-runtime"], help="Only show entries from a specific tooling class.")
    args = parser.parse_args()

    catalog = load_catalog()
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for record in catalog:
        try:
            item = build_result(record, online=not args.offline, with_release_notes=args.with_release_notes)
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
        rows.append(item)

    rows.sort(key=lambda row: row["id"])
    rows = filter_rows(rows, args.only_outdated, args.only_nonstandard, args.only_class)

    if args.json:
        payload: dict[str, Any] = {"installed": rows}
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
