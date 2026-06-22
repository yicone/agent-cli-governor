#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import traceback
from asyncio import wait_for
from pathlib import Path
from typing import Any

from nicegui import run, ui


ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "agent_cli_audit.py"
UPGRADE = ROOT / "agent_cli_upgrade.py"
SAMPLE_DATA = ROOT / "gui_sample_data.json"
AUDIT_TIMEOUT_SECONDS = 75
PLAN_TIMEOUT_SECONDS = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NiceGUI shell for agent-cli-governor")
    parser.add_argument("--reload", action="store_true", help="Enable NiceGUI hot reload for local GUI development")
    return parser.parse_args()


def run_json_command(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(args, text=True, capture_output=True, cwd=ROOT)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Command failed")
    return json.loads(completed.stdout)


def load_sample_rows() -> list[dict[str, Any]]:
    return json.loads(SAMPLE_DATA.read_text())["overview_rows"]


def row_dict(row: Any) -> dict[str, Any]:
    return row if isinstance(row, dict) else {}


def error_entries(rows: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if not isinstance(rows, list):
        return entries
    for row in rows:
        if isinstance(row, dict):
            entries.append({k: str(v) for k, v in row.items()})
        else:
            entries.append({"id": "unknown", "error": str(row)})
    return entries


def version_key(version: str | None) -> tuple[int, int, int, int] | None:
    if not version:
        return None
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$", version)
    if not match:
        return None
    major, minor, patch = (int(match.group(i)) for i in range(1, 4))
    suffix = match.group(4)
    stable_rank = 1 if not suffix else 0
    return (major, minor, patch, stable_rank)


def version_state_of(row: Any) -> str:
    item = row_dict(row)
    current = item.get("current_version")
    latest = item.get("latest_version")
    if not current or not latest:
        return "unknown"
    current_key = version_key(current)
    latest_key = version_key(latest)
    if current_key is None or latest_key is None:
        return "up-to-date" if current == latest else "different"
    if current_key < latest_key:
        return "outdated"
    if current_key > latest_key:
        return "ahead"
    return "up-to-date"


def is_outdated(row: Any) -> bool:
    return version_state_of(row) == "outdated"


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    safe_rows = [row_dict(row) for row in rows if isinstance(row, dict)]
    return {
        "outdated": sum(1 for row in safe_rows if is_outdated(row)),
        "ahead": sum(1 for row in safe_rows if version_state_of(row) == "ahead"),
        "recommended": sum(1 for row in safe_rows if row.get("channel_status") == "recommended"),
        "supported_only": sum(1 for row in safe_rows if row.get("channel_status") == "supported"),
        "nonstandard": sum(1 for row in safe_rows if row.get("channel_status") == "nonstandard"),
        "up_to_date": sum(1 for row in safe_rows if version_state_of(row) == "up-to-date"),
    }


def risk_of(row: dict[str, Any]) -> str:
    return release_summary_of(row_dict(row)).get("risk_level", "unknown")


def release_summary_of(row: dict[str, Any]) -> dict[str, Any]:
    summary = row_dict(row).get("release_summary")
    return summary if isinstance(summary, dict) else {}


def focus_upgrade_model() -> None:
    ui.run_javascript(
        """
        const target = document.getElementById('decision-model');
        if (!target) return;
        target.scrollIntoView({behavior: 'smooth', block: 'start'});
        const previous = target.style.boxShadow;
        target.style.boxShadow = '0 0 0 4px rgba(217, 123, 41, 0.45)';
        target.style.borderRadius = '12px';
        setTimeout(() => { target.style.boxShadow = previous; }, 1800);
        """
    )


class AppState:
    def __init__(self) -> None:
        self.audit_rows: list[dict[str, Any]] = []
        self.plan_rows: list[dict[str, Any]] = []
        self.selected_row: dict[str, Any] | None = None
        self.activity: list[str] = []
        self.audit_command = ""
        self.plan_command = ""
        self.current_class = "agent-cli"
        self.current_channel = "recommended"
        self.release_notes = True
        self.offline = False
        self.is_running = False
        self.status_filter = "all"
        self.only_outdated = False
        self.last_error = ""
        self.audit_errors: list[dict[str, str]] = []

    def log(self, message: str) -> None:
        self.activity.append(message)
        if len(self.activity) > 50:
            self.activity = self.activity[-50:]


state = AppState()


def build_audit_command() -> list[str]:
    args = ["python3", str(AUDIT), "--json", "--only-class", state.current_class]
    if state.release_notes:
        args.append("--with-release-notes")
    if state.offline:
        args.append("--offline")
    state.audit_command = " ".join(args)
    return args


def build_plan_command() -> list[str]:
    args = ["python3", str(UPGRADE), "--json", "--channel", state.current_channel]
    if state.offline:
        args.append("--offline")
    state.plan_command = " ".join(args)
    return args


def filter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [row_dict(row) for row in rows if isinstance(row, dict)]
    if state.current_channel == "recommended":
        filtered = [row for row in filtered if row.get("channel_status") == "recommended"]
    elif state.current_channel == "supported":
        filtered = [row for row in filtered if row.get("channel_status") in {"recommended", "supported"}]
    if state.status_filter != "all":
        filtered = [row for row in filtered if row.get("channel_status") == state.status_filter]
    if state.only_outdated:
        filtered = [row for row in filtered if is_outdated(row)]
    return filtered


def badge_classes(status: str) -> str:
    if status == "recommended":
        return "bg-green-100 text-green-800 px-2 py-1 rounded text-xs"
    if status == "supported":
        return "bg-amber-100 text-amber-800 px-2 py-1 rounded text-xs"
    if status == "nonstandard":
        return "bg-red-100 text-red-800 px-2 py-1 rounded text-xs"
    return "bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs"


def version_badge_classes(status: str) -> str:
    if status == "outdated":
        return "bg-red-100 text-red-800 px-2 py-1 rounded text-xs font-medium"
    if status == "up-to-date":
        return "bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-medium"
    if status == "ahead":
        return "bg-sky-100 text-sky-800 px-2 py-1 rounded text-xs font-medium"
    if status == "different":
        return "bg-amber-100 text-amber-800 px-2 py-1 rounded text-xs font-medium"
    return "bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs font-medium"


def version_badge_html(status: str) -> str:
    return f"<span class='{version_badge_classes(status)}'>{status}</span>"


def risk_badge_classes(risk: str) -> str:
    if risk == "high":
        return "bg-red-100 text-red-800 px-2 py-1 rounded text-xs font-medium"
    if risk == "medium":
        return "bg-amber-100 text-amber-800 px-2 py-1 rounded text-xs font-medium"
    if risk == "low":
        return "bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-medium"
    return "bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs font-medium"


def risk_badge_html(risk: str) -> str:
    return f"<span class='{risk_badge_classes(risk)}'>{risk}</span>"


def yes_no_badge_html(value: bool) -> str:
    label = "eligible" if value else "not eligible"
    classes = "bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-medium" if value else "bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs font-medium"
    return f"<span class='{classes}'>{label}</span>"


def render_command_text(container: ui.column, command: str) -> None:
    ui.textarea(value=command).props(
        'readonly outlined autogrow input-style="white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;"'
    ).classes("w-full")


def refresh_summary(summary_container: ui.column, rows: list[dict[str, Any]]) -> None:
    summary_container.clear()
    summary = summarize_rows(filter_rows(rows))
    with summary_container:
        with ui.column().classes("w-full gap-2"):
            ui.label("Version Status").classes("text-sm font-semibold text-gray-700")
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for label, value in [
                    ("Outdated", summary["outdated"]),
                    ("Up-to-date", summary["up_to_date"]),
                    ("Ahead", summary["ahead"]),
                ]:
                    with ui.card().classes("p-3 min-w-[140px]"):
                        ui.label(label).classes("text-xs text-gray-600")
                        ui.label(str(value)).classes("text-2xl font-semibold")

        with ui.column().classes("w-full gap-2 mt-2"):
            ui.label("Channel Policy").classes("text-sm font-semibold text-gray-700")
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for label, value in [
                    ("Recommended", summary["recommended"]),
                    ("Supported-only", summary["supported_only"]),
                    ("Nonstandard", summary["nonstandard"]),
                ]:
                    with ui.card().classes("p-3 min-w-[140px]"):
                        ui.label(label).classes("text-xs text-gray-600")
                        ui.label(str(value)).classes("text-2xl font-semibold")


def refresh_status_strip(container: ui.column, rows: list[dict[str, Any]]) -> None:
    container.clear()
    filtered_rows = filter_rows(rows)
    summary = summarize_rows(filtered_rows)
    items = [
        ("Recommended upgrades", sum(1 for row in filtered_rows if row.get("channel_status") == "recommended" and is_outdated(row)), "Routine in-channel upgrades on vendor-preferred paths.", "bg-green-600 text-white"),
        ("Supported review", sum(1 for row in filtered_rows if row.get("channel_status") == "supported"), "Working installs on allowed but less preferred channels.", "bg-amber-500 text-white"),
        ("Nonstandard review", summary["nonstandard"], "Manual review or migration is likely safer than routine upgrade.", "bg-red-600 text-white"),
    ]
    with container:
        ui.label("Action Queue").classes("text-sm font-semibold text-gray-700")
        with ui.row().classes("w-full gap-3"):
            for label, value, note, classes in items:
                with ui.card().classes(f"flex-1 p-4 {classes}"):
                    ui.label(label).classes("text-sm opacity-90")
                    ui.label(str(value)).classes("text-3xl font-bold")
                    ui.label(note).classes("text-xs opacity-90 mt-2")


def render_details(container: ui.column, row: dict[str, Any] | None) -> None:
    container.clear()
    item = row_dict(row)
    if not item:
        with container:
            ui.label("Select a row to inspect details.").classes("text-gray-600")
        return
    with container:
        ui.label(item.get("id", "unknown")).classes("text-xl font-semibold")
        ui.label(f"class: {item.get('tooling_class', 'agent-cli')}")
        ui.label(f"current: {item.get('current_version', 'unknown')}")
        ui.label(f"latest: {item.get('latest_version', 'unknown')}")
        ui.html(version_badge_html(version_state_of(item)))
        ui.label(f"channel: {item.get('normalized_channel', 'unknown')}")
        ui.html(f"<span class='{badge_classes(item.get('channel_status', 'unknown'))}'>{item.get('channel_status', 'unknown')}</span>")
        ui.label("release risk").classes("text-sm text-gray-600")
        ui.html(risk_badge_html(risk_of(item)))
        eligible = (
            item.get("channel_status") in {"recommended", "supported"}
            and is_outdated(item)
            and item.get("update_command") != "See official install docs"
        )
        ui.label("upgrade plan eligibility").classes("text-sm text-gray-600")
        ui.html(yes_no_badge_html(eligible))
        if item.get("notes"):
            ui.markdown(f"**Notes**: {item['notes']}")
        summary = release_summary_of(item)
        if summary.get("highlights"):
            ui.label("Highlights").classes("font-semibold mt-2")
            for highlight in summary["highlights"][:2]:
                ui.markdown(f"- {highlight}")
        ui.label("Commands").classes("font-semibold mt-2")
        with ui.row().classes("gap-3 flex-wrap"):
            ui.button("Copy upgrade", on_click=lambda cmd=item.get("update_command", ""): ui.run_javascript(f"navigator.clipboard.writeText({cmd!r})"))
            if item.get("migration_command"):
                ui.button("Copy migration", on_click=lambda cmd=item["migration_command"]: ui.run_javascript(f"navigator.clipboard.writeText({cmd!r})"))
        render_command_text(container, item.get("update_command", ""))
        if item.get("migration_command"):
            render_command_text(container, item["migration_command"])


def render_audit_warnings(container: ui.column, errors: list[dict[str, str]]) -> None:
    container.clear()
    with container:
        with ui.card().classes("w-full p-4"):
            ui.label("Audit Warnings").classes("text-lg font-semibold")
            if not errors:
                ui.label("No audit warnings for the current result.").classes("text-sm text-gray-600")
                return
            ui.label("These entries failed partially during audit, but the rest of the audit result is still usable.").classes("text-sm text-gray-600")
            for item in errors:
                with ui.card().classes("w-full p-3 mt-2"):
                    ui.label(item.get("id", "unknown")).classes("font-semibold")
                    if item.get("name"):
                        ui.label(item["name"]).classes("text-sm text-gray-600")
                    ui.textarea(value=item.get("error", "unknown error")).props(
                        'readonly outlined autogrow input-style="white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;"'
                    ).classes("w-full")


def render_table(container: ui.column, rows: list[dict[str, Any]], details: ui.column) -> None:
    container.clear()
    rows = filter_rows(rows)
    table_rows = []
    for row in rows:
        item = row_dict(row)
        if not item:
            continue
        version_state = version_state_of(item)
        table_rows.append(
            {
                **item,
                "version_state": version_state,
                "risk": risk_of(item),
            }
        )
    with container:
        table = ui.aggrid(
            {
                "defaultColDef": {"resizable": True, "sortable": True, "filter": True},
                "columnDefs": [
                    {"field": "id", "headerName": "Tool"},
                    {"field": "tooling_class", "headerName": "Class"},
                    {"field": "current_version", "headerName": "Current"},
                    {"field": "latest_version", "headerName": "Latest"},
                    {
                        "field": "version_state",
                        "headerName": "Version",
                        ":cellRenderer": """
                        params => {
                          const status = params.value || 'unknown';
                          const classes = {
                            'outdated': 'bg-red-100 text-red-800 px-2 py-1 rounded text-xs font-medium',
                            'up-to-date': 'bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-medium',
                            'ahead': 'bg-sky-100 text-sky-800 px-2 py-1 rounded text-xs font-medium',
                            'different': 'bg-amber-100 text-amber-800 px-2 py-1 rounded text-xs font-medium',
                            'unknown': 'bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs font-medium',
                          };
                          return `<span class="${classes[status] || classes['unknown']}">${status}</span>`;
                        }
                        """,
                    },
                    {"field": "normalized_channel", "headerName": "Channel"},
                    {"field": "channel_status", "headerName": "Status"},
                    {
                        "field": "risk",
                        "headerName": "Release Risk",
                        ":cellRenderer": """
                        params => {
                          const risk = params.value || 'unknown';
                          const classes = {
                            'high': 'bg-red-100 text-red-800 px-2 py-1 rounded text-xs font-medium',
                            'medium': 'bg-amber-100 text-amber-800 px-2 py-1 rounded text-xs font-medium',
                            'low': 'bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-medium',
                            'unknown': 'bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs font-medium',
                          };
                          return `<span class="${classes[risk] || classes['unknown']}">${risk}</span>`;
                        }
                        """,
                    },
                ],
                "rowData": table_rows,
                "rowSelection": "single",
                "animateRows": True,
            }
        ).classes("w-full h-[460px]")

        def handle_click(event: Any) -> None:
            args = getattr(event, "args", None) or {}
            row = args.get("data")
            if row is None and isinstance(args.get("rowIndex"), int):
                idx = args["rowIndex"]
                if 0 <= idx < len(table_rows):
                    row = table_rows[idx]
            if row:
                state.selected_row = row
                render_details(details, state.selected_row)

        table.on("cellClicked", handle_click)
        table.on("rowClicked", handle_click)


async def run_audit(
    summary_row: ui.column,
    status_strip: ui.column,
    table_col: ui.column,
    details: ui.column,
    warnings_col: ui.column,
    activity_col: ui.log,
    status_label: ui.label,
    spinner: ui.spinner,
) -> None:
    if state.is_running:
        ui.notify("A task is already running", type="warning")
        return
    state.is_running = True
    stage = "init"
    try:
        state.log("Running audit...")
        activity_col.push("Running audit...")
        status_label.set_text("Running audit...")
        spinner.visible = True
        stage = "command"
        payload = await wait_for(asyncio.to_thread(run_json_command, build_audit_command()), timeout=AUDIT_TIMEOUT_SECONDS)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected audit payload type: {type(payload).__name__}")
        stage = "payload"
        state.audit_rows = payload.get("installed") or []
        state.audit_errors = error_entries(payload.get("errors"))
        stage = "summary"
        refresh_summary(summary_row, state.audit_rows)
        stage = "action_queue"
        refresh_status_strip(status_strip, state.audit_rows)
        stage = "table"
        render_table(table_col, state.audit_rows, details)
        stage = "details"
        render_details(details, None)
        stage = "warnings"
        render_audit_warnings(warnings_col, state.audit_errors)
        stage = "complete"
        activity_col.push(
            f"Audit completed. Channel={state.current_channel}, class={state.current_class}, rows={len(filter_rows(state.audit_rows))}, warnings={len(state.audit_errors)}."
        )
        status_label.set_text(f"Audit completed. {len(filter_rows(state.audit_rows))} row(s) shown. Warnings: {len(state.audit_errors)}.")
        ui.notify(f"Audit completed ({len(state.audit_errors)} warning(s))", type="positive")
    except TimeoutError:
        activity_col.push(f"Audit timed out after {AUDIT_TIMEOUT_SECONDS}s. Try enabling Offline mode for a quicker local-only check.")
        status_label.set_text("Audit timed out.")
        ui.notify("Audit timed out. Try Offline mode.", type="warning")
    except Exception as exc:
        state.last_error = str(exc)
        activity_col.push(f"Audit failed during {stage}: {exc}")
        activity_col.push(traceback.format_exc())
        status_label.set_text(f"Audit failed during {stage}.")
        ui.notify(f"Audit failed during {stage}: {exc}", type="negative")
    finally:
        spinner.visible = False
        state.is_running = False


def render_plan_summary(container: ui.column, plan_rows: list[dict[str, Any]]) -> None:
    container.clear()
    with container:
        ui.label("Upgrade plans appear only after `Run Upgrade Plan`, and only for entries that are both outdated and on a recommended or supported channel.").classes("text-sm text-gray-600")
        if not plan_rows:
            ui.label("No upgrade candidates for the current filters. Selecting a table row alone does not populate this panel.").classes("text-gray-600")
            return
        for row in plan_rows:
            item = row_dict(row)
            if not item:
                continue
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(f"{item.get('id', 'unknown')}: {item.get('current_version')} -> {item.get('latest_version')}").classes("font-semibold")
                    ui.html(f"<span class='{badge_classes(item.get('channel_status', 'unknown'))}'>{item.get('channel_status', 'unknown')}</span>")
                ui.label(f"release risk: {risk_of(item)}").classes("text-sm text-gray-700")
                summary = release_summary_of(item)
                risk_terms = summary.get("risk_terms", [])
                if risk_terms:
                    ui.label(f"risk terms: {', '.join(risk_terms[:5])}").classes("text-xs text-gray-600")
                for highlight in summary.get("highlights", [])[:2]:
                    ui.markdown(f"- {highlight}")
                render_command_text(container, item.get("update_command", ""))


async def run_plan(activity_col: ui.log, status_label: ui.label, spinner: ui.spinner, plan_container: ui.column) -> None:
    if state.is_running:
        ui.notify("A task is already running", type="warning")
        return
    state.is_running = True
    try:
        activity_col.push("Running upgrade plan...")
        status_label.set_text("Running upgrade plan...")
        spinner.visible = True
        payload = await wait_for(asyncio.to_thread(run_json_command, build_plan_command()), timeout=PLAN_TIMEOUT_SECONDS)
        state.plan_rows = payload["plan"]
        render_plan_summary(plan_container, state.plan_rows)
        activity_col.push(f"Upgrade plan completed with {len(state.plan_rows)} candidate(s).")
        status_label.set_text(f"Upgrade plan completed with {len(state.plan_rows)} candidate(s).")
        ui.notify(f"Upgrade plan completed: {len(state.plan_rows)} candidate(s)", type="positive")
    except TimeoutError:
        activity_col.push(f"Upgrade plan timed out after {PLAN_TIMEOUT_SECONDS}s. Try enabling Offline mode first.")
        status_label.set_text("Upgrade plan timed out.")
        ui.notify("Upgrade plan timed out. Try Offline mode.", type="warning")
    except Exception as exc:
        state.last_error = str(exc)
        activity_col.push(f"Upgrade plan failed: {exc}")
        status_label.set_text("Upgrade plan failed.")
        ui.notify(f"Upgrade plan failed: {exc}", type="negative")
    finally:
        spinner.visible = False
        state.is_running = False


ui.page_title("agent-cli-governor")
ui.colors(primary="#1f4d3d", secondary="#d97b29", accent="#6f8f77")

with ui.header().classes("items-center justify-between px-6 py-3 bg-white shadow-sm"):
    ui.label("agent-cli-governor").classes("text-xl font-semibold")
    ui.label("CLI-first local governance for agent tools").classes("text-sm text-gray-600")

with ui.tabs().classes("w-full px-6") as tabs:
    overview_tab = ui.tab("Overview")
    console_tab = ui.tab("Console")

with ui.tab_panels(tabs, value=overview_tab).classes("w-full"):
    with ui.tab_panel(overview_tab):
        rows = load_sample_rows()
        with ui.column().classes("w-full max-w-6xl mx-auto gap-6 p-6"):
            with ui.card().classes("w-full p-6"):
                ui.label("Govern agent CLIs like operational dependencies, not just packages.").classes("text-3xl font-bold")
                ui.label("This project checks version drift, install-channel drift, and changelog risk before you decide to upgrade.").classes("text-base text-gray-700")
                with ui.row():
                    ui.button("View Console", on_click=lambda: tabs.set_value(console_tab))
                    ui.button("Read Install-channel Model", on_click=focus_upgrade_model).props("outline")
            with ui.column().classes("w-full gap-2"):
                ui.label("Why upgrades are hard").classes("text-xl font-semibold")
                ui.label(
                    "These are the three recurring problems the project is designed to reduce before you decide whether to upgrade or migrate a CLI."
                ).classes("text-sm text-gray-600")
            with ui.grid(columns=3).classes("w-full gap-4"):
                for title, body in [
                    ("Version drift", "Installed CLIs lag upstream and are easy to forget."),
                    ("Install-channel drift", "A CLI may still work while no longer following vendor guidance."),
                    ("Opaque changelogs", "Upstream changes can affect auth, providers, plugins, and session behavior."),
                ]:
                    with ui.card().classes("p-4"):
                        ui.label(title).classes("text-lg font-semibold")
                        ui.label(body).classes("text-sm text-gray-700")
            with ui.card().classes("w-full p-5 border border-gray-200").props("id=decision-model"):
                with ui.column().classes("w-full gap-2"):
                    ui.label("Install-channel Upgrade Model").classes("text-xl font-semibold")
                    ui.label(
                        "agent-cli-governor classifies each installed CLI by how its current install channel relates to vendor guidance. "
                        "This classification drives whether the tool recommends a routine upgrade, a broader review, or a migration."
                    ).classes("text-sm text-gray-600")
                with ui.row().classes("w-full gap-4 mt-3"):
                    for title, body, color in [
                        ("Status: recommended", "The current install channel matches the vendor-preferred path for routine upgrades.", "bg-green-50"),
                        ("Status: supported", "The current channel still works and is supported, but it is no longer the preferred path.", "bg-amber-50"),
                        ("Status: nonstandard", "The current install method has drifted far enough that manual review or migration is the safer next step.", "bg-red-50"),
                    ]:
                        with ui.card().classes(f"p-4 flex-1 {color}"):
                            ui.label(title).classes("text-lg font-semibold")
                            ui.label(body).classes("text-sm text-gray-700")
            with ui.card().classes("w-full p-4"):
                ui.label("Example Audit").classes("text-xl font-semibold")
                preview = ui.column().classes("w-full")
                details = ui.column().classes("w-full")
                render_table(preview, rows, details)
    with ui.tab_panel(console_tab):
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-6"):
            with ui.card().classes("w-full p-4"):
                with ui.row().classes("items-center gap-3 w-full flex-wrap"):
                    class_select = ui.select(["agent-cli", "tooling-runtime"], value="agent-cli", label="Class")
                    channel_select = ui.select(["recommended", "supported", "all"], value="recommended", label="Channel")
                    release_notes = ui.switch("Release Notes", value=True)
                    offline = ui.switch("Offline", value=False)

                    summary_row = ui.column().classes("w-full gap-2 mt-4")
                    status_strip = ui.column().classes("w-full gap-2 mt-2")
                    table_col = ui.column().classes("w-full")
                    activity_log = ui.log().classes("w-full h-32")
                    status_label = ui.label("Idle").classes("text-sm text-gray-600 mt-2")
                    spinner = ui.spinner(size="md")
                    spinner.visible = False

                    def sync_state() -> None:
                        state.current_class = class_select.value
                        state.current_channel = channel_select.value
                        state.release_notes = bool(release_notes.value)
                        state.offline = bool(offline.value)

                    def refresh_current_table() -> None:
                        if state.audit_rows:
                            refresh_summary(summary_row, state.audit_rows)
                            refresh_status_strip(status_strip, state.audit_rows)
                            render_table(table_col, state.audit_rows, details_content)
                            render_audit_warnings(warnings_col, state.audit_errors)

                    with ui.row().classes("gap-2 flex-wrap"):
                        async def handle_audit() -> None:
                            sync_state()
                            await run_audit(summary_row, status_strip, table_col, details_content, warnings_col, activity_log, status_label, spinner)

                        async def handle_plan() -> None:
                            sync_state()
                            await run_plan(activity_log, status_label, spinner, plan_content)

                        ui.button("Run Audit", on_click=handle_audit)
                        ui.button("Run Upgrade Plan", on_click=handle_plan)
                        ui.button("Refresh", on_click=handle_audit)
                        ui.button("Copy Summary", on_click=lambda: ui.run_javascript(f"navigator.clipboard.writeText({json.dumps({'audit': state.audit_command, 'plan': state.plan_command})!r})"))

                    with ui.row().classes("gap-6 mt-3 items-end w-full justify-between"):
                        with ui.row().classes("gap-4 items-end"):
                            status_select = ui.select(["all", "recommended", "supported", "nonstandard"], value="all", label="Status Filter").classes("min-w-[220px]")
                            outdated_only = ui.switch("Only outdated", value=False)

                        def sync_filters() -> None:
                            state.status_filter = status_select.value
                            state.only_outdated = bool(outdated_only.value)
                            refresh_current_table()

                        status_select.on("update:model-value", lambda _: sync_filters())
                        outdated_only.on("update:model-value", lambda _: sync_filters())

            with ui.row().classes("w-full gap-4 items-start"):
                with ui.column().classes("flex-1 gap-4"):
                    with ui.card().classes("w-full p-4"):
                        ui.label("Command Preview").classes("text-lg font-semibold")
                        ui.label("Audit")
                        audit_code = ui.textarea().props("readonly outlined autogrow").classes("w-full")
                        ui.label("Upgrade Plan")
                        plan_code = ui.textarea().props("readonly outlined autogrow").classes("w-full")

                        def refresh_commands() -> None:
                            sync_state()
                            build_audit_command()
                            build_plan_command()
                            audit_code.value = state.audit_command
                            plan_code.value = state.plan_command

                        for widget in [class_select, channel_select, release_notes, offline]:
                            widget.on("update:model-value", lambda _: refresh_commands())
                        refresh_commands()

                    warnings_col = ui.column().classes("w-full")
                    render_audit_warnings(warnings_col, [])

                with ui.column().classes("flex-1 gap-4"):
                    with ui.card().classes("w-full p-4"):
                        ui.label("Details").classes("text-lg font-semibold")
                        details_content = ui.column().classes("w-full")
                        render_details(details_content, None)

                    with ui.card().classes("w-full p-4"):
                        ui.label("Upgrade Plan").classes("text-lg font-semibold")
                        plan_content = ui.column().classes("w-full")
                        render_plan_summary(plan_content, [])

if __name__ in {"__main__", "__mp_main__"}:
    args = parse_args()
    reload_mode = args.reload
    print(f"Hot reload: {'enabled' if reload_mode else 'disabled'}")
    if not reload_mode:
        print("Hint: run `python3 gui.py --reload` for local GUI hot reload.")
    try:
        ui.run(title="agent-cli-governor", reload=reload_mode)
    except KeyboardInterrupt:
        pass
