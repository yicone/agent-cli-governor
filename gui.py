#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from nicegui import ui


ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "agent_cli_audit.py"
UPGRADE = ROOT / "agent_cli_upgrade.py"
SAMPLE_DATA = ROOT / "gui_sample_data.json"


def run_json_command(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(args, text=True, capture_output=True, cwd=ROOT)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Command failed")
    return json.loads(completed.stdout)


def load_sample_rows() -> list[dict[str, Any]]:
    return json.loads(SAMPLE_DATA.read_text())["overview_rows"]


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "outdated": sum(1 for row in rows if row.get("current_version") and row.get("latest_version") and row["current_version"] != row["latest_version"]),
        "recommended": sum(1 for row in rows if row.get("channel_status") == "recommended"),
        "supported_only": sum(1 for row in rows if row.get("channel_status") == "supported"),
        "nonstandard": sum(1 for row in rows if row.get("channel_status") == "nonstandard"),
        "current": sum(1 for row in rows if row.get("current_version") == row.get("latest_version")),
    }


def risk_of(row: dict[str, Any]) -> str:
    return row.get("release_summary", {}).get("risk_level", "unknown")


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
    state.plan_command = " ".join(args)
    return args


def refresh_summary(summary_row: ui.row, rows: list[dict[str, Any]]) -> None:
    summary_row.clear()
    summary = summarize_rows(rows)
    with summary_row:
        for label, value in [
            ("Outdated", summary["outdated"]),
            ("Recommended", summary["recommended"]),
            ("Supported-only", summary["supported_only"]),
            ("Nonstandard", summary["nonstandard"]),
            ("Current", summary["current"]),
        ]:
            with ui.card().classes("p-3 min-w-[120px]"):
                ui.label(label).classes("text-xs text-gray-600")
                ui.label(str(value)).classes("text-2xl font-semibold")


def render_details(container: ui.column, row: dict[str, Any] | None) -> None:
    container.clear()
    if not row:
        with container:
            ui.label("Select a row to inspect details.").classes("text-gray-600")
        return
    with container:
        ui.label(row["id"]).classes("text-xl font-semibold")
        ui.label(f"class: {row.get('tooling_class', 'agent-cli')}")
        ui.label(f"current: {row.get('current_version', 'unknown')}")
        ui.label(f"latest: {row.get('latest_version', 'unknown')}")
        ui.label(f"channel: {row.get('normalized_channel', 'unknown')}")
        ui.label(f"status: {row.get('channel_status', 'unknown')}")
        ui.label(f"risk: {risk_of(row)}")
        if row.get("notes"):
            ui.markdown(f"**Notes**: {row['notes']}")
        if row.get("release_summary", {}).get("highlights"):
            ui.label("Highlights").classes("font-semibold mt-2")
            for item in row["release_summary"]["highlights"][:2]:
                ui.markdown(f"- {item}")
        ui.label("Commands").classes("font-semibold mt-2")
        with ui.row():
            ui.button("Copy upgrade", on_click=lambda cmd=row.get("update_command", ""): ui.run_javascript(f"navigator.clipboard.writeText({cmd!r})"))
            if row.get("migration_command"):
                ui.button("Copy migration", on_click=lambda cmd=row["migration_command"]: ui.run_javascript(f"navigator.clipboard.writeText({cmd!r})"))
        ui.code(row.get("update_command", ""))
        if row.get("migration_command"):
            ui.code(row["migration_command"])


def render_table(container: ui.column, rows: list[dict[str, Any]], details: ui.column) -> None:
    container.clear()
    columns = [
        {"name": "id", "label": "Tool", "field": "id", "sortable": True},
        {"name": "tooling_class", "label": "Class", "field": "tooling_class", "sortable": True},
        {"name": "current_version", "label": "Current", "field": "current_version", "sortable": True},
        {"name": "latest_version", "label": "Latest", "field": "latest_version", "sortable": True},
        {"name": "normalized_channel", "label": "Channel", "field": "normalized_channel", "sortable": True},
        {"name": "channel_status", "label": "Status", "field": "channel_status", "sortable": True},
        {"name": "risk", "label": "Risk", "field": "risk", "sortable": True},
    ]
    table_rows = []
    for row in rows:
        table_rows.append({**row, "risk": risk_of(row)})
    with container:
        table = ui.aggrid(
            {
                "defaultColDef": {"resizable": True, "sortable": True, "filter": True},
                "columnDefs": [
                    {"field": "id", "headerName": "Tool"},
                    {"field": "tooling_class", "headerName": "Class"},
                    {"field": "current_version", "headerName": "Current"},
                    {"field": "latest_version", "headerName": "Latest"},
                    {"field": "normalized_channel", "headerName": "Channel"},
                    {"field": "channel_status", "headerName": "Status"},
                    {"field": "risk", "headerName": "Risk"},
                ],
                "rowData": table_rows,
                "rowSelection": "single",
                "animateRows": True,
            }
        ).classes("w-full h-[460px]")

        def handle_select(_: Any) -> None:
            selected = table.get_selected_rows()
            if selected:
                state.selected_row = selected[0]
                render_details(details, state.selected_row)

        table.on("selectionChanged", handle_select)


def run_audit(summary_row: ui.row, table_col: ui.column, details: ui.column, activity_col: ui.log) -> None:
    try:
        state.log("Running audit...")
        activity_col.push("Running audit...")
        payload = run_json_command(build_audit_command())
        state.audit_rows = payload["installed"]
        refresh_summary(summary_row, state.audit_rows)
        render_table(table_col, state.audit_rows, details)
        render_details(details, None)
        activity_col.push("Audit completed.")
        ui.notify("Audit completed", type="positive")
    except Exception as exc:
        activity_col.push(f"Audit failed: {exc}")
        ui.notify(f"Audit failed: {exc}", type="negative")


def run_plan(activity_col: ui.log) -> None:
    try:
        activity_col.push("Running upgrade plan...")
        payload = run_json_command(build_plan_command())
        state.plan_rows = payload["plan"]
        activity_col.push(f"Upgrade plan completed with {len(state.plan_rows)} candidate(s).")
        ui.notify(f"Upgrade plan completed: {len(state.plan_rows)} candidate(s)", type="positive")
    except Exception as exc:
        activity_col.push(f"Upgrade plan failed: {exc}")
        ui.notify(f"Upgrade plan failed: {exc}", type="negative")


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
                    ui.button("Read Upgrade Model", on_click=lambda: ui.navigate.to("#decision-model")).props("outline")
            with ui.grid(columns=3).classes("w-full gap-4"):
                for title, body in [
                    ("Version drift", "Installed CLIs lag upstream and are easy to forget."),
                    ("Install-channel drift", "A CLI may still work while no longer following vendor guidance."),
                    ("Opaque changelogs", "Upstream changes can affect auth, providers, plugins, and session behavior."),
                ]:
                    with ui.card().classes("p-4"):
                        ui.label(title).classes("text-lg font-semibold")
                        ui.label(body).classes("text-sm text-gray-700")
            with ui.row().classes("w-full gap-4").props("id=decision-model"):
                for title, body, color in [
                    ("recommended", "Vendor-preferred path for routine upgrades.", "bg-green-50"),
                    ("supported", "Still supported, but not currently preferred.", "bg-amber-50"),
                    ("nonstandard", "Needs manual review or migration.", "bg-red-50"),
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
                with ui.row().classes("items-center gap-3 w-full"):
                    class_select = ui.select(["agent-cli", "tooling-runtime"], value="agent-cli", label="Class")
                    channel_select = ui.select(["recommended", "supported", "all"], value="recommended", label="Channel")
                    release_notes = ui.switch("Release Notes", value=True)
                    offline = ui.switch("Offline", value=False)

                    summary_row = ui.row().classes("w-full gap-3 mt-4")
                    table_col = ui.column().classes("w-full")
                    details_col = ui.column().classes("w-full")
                    activity_log = ui.log().classes("w-full h-32")

                    def sync_state() -> None:
                        state.current_class = class_select.value
                        state.current_channel = channel_select.value
                        state.release_notes = bool(release_notes.value)
                        state.offline = bool(offline.value)

                    with ui.row().classes("gap-2"):
                        ui.button("Run Audit", on_click=lambda: (sync_state(), run_audit(summary_row, table_col, details_col, activity_log)))
                        ui.button("Run Upgrade Plan", on_click=lambda: (sync_state(), run_plan(activity_log)))
                        ui.button("Refresh", on_click=lambda: (sync_state(), run_audit(summary_row, table_col, details_col, activity_log)))
                        ui.button("Copy Summary", on_click=lambda: ui.run_javascript(f"navigator.clipboard.writeText({json.dumps({'audit': state.audit_command, 'plan': state.plan_command})!r})"))

            with ui.grid(columns=2).classes("w-full gap-4"):
                with ui.card().classes("p-4"):
                    ui.label("Command Preview").classes("text-lg font-semibold")
                    ui.label("Audit")
                    audit_code = ui.code("")
                    ui.label("Upgrade Plan")
                    plan_code = ui.code("")

                    def refresh_commands() -> None:
                        sync_state()
                        build_audit_command()
                        build_plan_command()
                        audit_code.set_content(state.audit_command)
                        plan_code.set_content(state.plan_command)

                    for widget in [class_select, channel_select, release_notes, offline]:
                        widget.on("update:model-value", lambda _: refresh_commands())
                    refresh_commands()

                with ui.card().classes("p-4"):
                    ui.label("Details").classes("text-lg font-semibold")
                    render_details(details_col, None)


ui.run(title="agent-cli-governor", reload=False)
