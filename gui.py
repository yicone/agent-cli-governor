#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from asyncio import wait_for
from pathlib import Path
from typing import Any

from nicegui import run, ui


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
    return release_summary_of(row).get("risk_level", "unknown")


def release_summary_of(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("release_summary")
    return summary if isinstance(summary, dict) else {}


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
    filtered = rows
    if state.current_channel == "recommended":
        filtered = [row for row in filtered if row.get("channel_status") == "recommended"]
    elif state.current_channel == "supported":
        filtered = [row for row in filtered if row.get("channel_status") in {"recommended", "supported"}]
    if state.status_filter != "all":
        filtered = [row for row in filtered if row.get("channel_status") == state.status_filter]
    if state.only_outdated:
        filtered = [
            row for row in filtered
            if row.get("current_version") and row.get("latest_version") and row.get("current_version") != row.get("latest_version")
        ]
    return filtered


def badge_classes(status: str) -> str:
    if status == "recommended":
        return "bg-green-100 text-green-800 px-2 py-1 rounded text-xs"
    if status == "supported":
        return "bg-amber-100 text-amber-800 px-2 py-1 rounded text-xs"
    if status == "nonstandard":
        return "bg-red-100 text-red-800 px-2 py-1 rounded text-xs"
    return "bg-gray-100 text-gray-700 px-2 py-1 rounded text-xs"


def refresh_summary(summary_row: ui.row, rows: list[dict[str, Any]]) -> None:
    summary_row.clear()
    summary = summarize_rows(filter_rows(rows))
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


def refresh_status_strip(container: ui.row, rows: list[dict[str, Any]]) -> None:
    container.clear()
    summary = summarize_rows(filter_rows(rows))
    items = [
        ("Recommended upgrades", max(summary["recommended"] - summary["current"], 0), "bg-green-600 text-white"),
        ("Supported-only", summary["supported_only"], "bg-amber-500 text-white"),
        ("Nonstandard", summary["nonstandard"], "bg-red-600 text-white"),
    ]
    with container:
        for label, value, classes in items:
            with ui.card().classes(f"flex-1 p-4 {classes}"):
                ui.label(label).classes("text-sm opacity-90")
                ui.label(str(value)).classes("text-3xl font-bold")


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
        ui.html(f"<span class='{badge_classes(row.get('channel_status', 'unknown'))}'>{row.get('channel_status', 'unknown')}</span>")
        ui.label(f"risk: {risk_of(row)}")
        if row.get("notes"):
            ui.markdown(f"**Notes**: {row['notes']}")
        summary = release_summary_of(row)
        if summary.get("highlights"):
            ui.label("Highlights").classes("font-semibold mt-2")
            for item in summary["highlights"][:2]:
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
    rows = filter_rows(rows)
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
    summary_row: ui.row,
    status_strip: ui.row,
    table_col: ui.column,
    details: ui.column,
    activity_col: ui.log,
    status_label: ui.label,
    spinner: ui.spinner,
) -> None:
    if state.is_running:
        ui.notify("A task is already running", type="warning")
        return
    state.is_running = True
    try:
        state.log("Running audit...")
        activity_col.push("Running audit...")
        status_label.set_text("Running audit...")
        spinner.visible = True
        payload = await wait_for(run.io_bound(run_json_command, build_audit_command()), timeout=45)
        state.audit_rows = payload.get("installed") or []
        refresh_summary(summary_row, state.audit_rows)
        refresh_status_strip(status_strip, state.audit_rows)
        render_table(table_col, state.audit_rows, details)
        render_details(details, None)
        activity_col.push(f"Audit completed. Channel={state.current_channel}, class={state.current_class}, rows={len(filter_rows(state.audit_rows))}.")
        status_label.set_text(f"Audit completed. {len(filter_rows(state.audit_rows))} row(s) shown.")
        ui.notify("Audit completed", type="positive")
    except TimeoutError:
        activity_col.push("Audit timed out. Try enabling Offline mode for a quicker local-only check.")
        status_label.set_text("Audit timed out.")
        ui.notify("Audit timed out. Try Offline mode.", type="warning")
    except Exception as exc:
        state.last_error = str(exc)
        activity_col.push(f"Audit failed: {exc}")
        status_label.set_text("Audit failed.")
        ui.notify(f"Audit failed: {exc}", type="negative")
    finally:
        spinner.visible = False
        state.is_running = False


def render_plan_summary(container: ui.column, plan_rows: list[dict[str, Any]]) -> None:
    container.clear()
    with container:
        if not plan_rows:
            ui.label("No upgrade candidates for the current filters.").classes("text-gray-600")
            return
        for row in plan_rows:
            with ui.card().classes("w-full p-3"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(f"{row['id']}: {row.get('current_version')} -> {row.get('latest_version')}").classes("font-semibold")
                    ui.html(f"<span class='{badge_classes(row.get('channel_status', 'unknown'))}'>{row.get('channel_status', 'unknown')}</span>")
                ui.label(f"risk: {risk_of(row)}").classes("text-sm text-gray-700")
                summary = release_summary_of(row)
                risk_terms = summary.get("risk_terms", [])
                if risk_terms:
                    ui.label(f"risk terms: {', '.join(risk_terms[:5])}").classes("text-xs text-gray-600")
                for highlight in summary.get("highlights", [])[:2]:
                    ui.markdown(f"- {highlight}")
                ui.markdown(f"```\n{row.get('update_command', '')}\n```")


async def run_plan(activity_col: ui.log, status_label: ui.label, spinner: ui.spinner, plan_container: ui.column) -> None:
    if state.is_running:
        ui.notify("A task is already running", type="warning")
        return
    state.is_running = True
    try:
        activity_col.push("Running upgrade plan...")
        status_label.set_text("Running upgrade plan...")
        spinner.visible = True
        payload = await wait_for(run.io_bound(run_json_command, build_plan_command()), timeout=45)
        state.plan_rows = payload["plan"]
        render_plan_summary(plan_container, state.plan_rows)
        activity_col.push(f"Upgrade plan completed with {len(state.plan_rows)} candidate(s).")
        status_label.set_text(f"Upgrade plan completed with {len(state.plan_rows)} candidate(s).")
        ui.notify(f"Upgrade plan completed: {len(state.plan_rows)} candidate(s)", type="positive")
    except TimeoutError:
        activity_col.push("Upgrade plan timed out. Try enabling Offline mode first.")
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
                with ui.row().classes("items-center gap-3 w-full flex-wrap"):
                    class_select = ui.select(["agent-cli", "tooling-runtime"], value="agent-cli", label="Class")
                    channel_select = ui.select(["recommended", "supported", "all"], value="recommended", label="Channel")
                    release_notes = ui.switch("Release Notes", value=True)
                    offline = ui.switch("Offline", value=False)

                    summary_row = ui.row().classes("w-full gap-3 mt-4")
                    status_strip = ui.row().classes("w-full gap-3 mt-2")
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

                    with ui.row().classes("gap-2 flex-wrap"):
                        async def handle_audit() -> None:
                            sync_state()
                            await run_audit(summary_row, status_strip, table_col, details_content, activity_log, status_label, spinner)

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

            with ui.grid(columns=2).classes("w-full gap-4 items-start"):
                with ui.card().classes("p-4"):
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

                with ui.card().classes("p-4"):
                    ui.label("Details").classes("text-lg font-semibold")
                    details_content = ui.column().classes("w-full")
                    render_details(details_content, None)

                with ui.card().classes("p-4"):
                    ui.label("Upgrade Plan").classes("text-lg font-semibold")
                    plan_content = ui.column().classes("w-full")
                    render_plan_summary(plan_content, [])


ui.run(title="agent-cli-governor", reload=False)
