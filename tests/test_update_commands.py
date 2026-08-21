import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_cli_audit import (
    binary_container,
    check_node_runtime,
    detect_channel,
    get_latest_from_source,
    get_update_command,
    release_platform,
    run,
    system_proxy_env,
    upgrade_guidance,
)


class UpgradeGuidanceTests(unittest.TestCase):
    def test_uses_channel_upgrade_when_native_ownership_is_unverified(self) -> None:
        record = {"id": "copilot", "npm_package": "@github/copilot"}

        guidance = upgrade_guidance(record, "npm", "recommended", None)

        self.assertEqual(guidance["command"], "mise exec node -- npm install -g @github/copilot@latest")
        self.assertEqual(guidance["kind"], "channel_update")
        self.assertEqual(guidance["alternatives"][0]["command"], "copilot update")

    def test_uses_explicit_method_for_kilo_and_opencode(self) -> None:
        cases = {
            "kilocode": "mise exec node -- kilo upgrade --method npm",
            "opencode": "opencode upgrade --method brew",
        }

        for tool, expected in cases.items():
            with self.subTest(tool=tool):
                channel = "npm" if tool == "kilocode" else "brew-core"
                guidance = upgrade_guidance({"id": tool}, channel, "recommended", None)
                self.assertEqual(guidance["command"], expected)
                self.assertEqual(guidance["kind"], "native_channel_update")

    def test_uses_official_installer_for_codex_script_install(self) -> None:
        guidance = upgrade_guidance({"id": "codex"}, "script", "recommended", None)

        self.assertEqual(guidance["kind"], "official_installer")
        self.assertIn("chatgpt.com/codex/install.sh", guidance["command"])

    def test_parses_codex_standalone_latest_from_official_release_tag(self) -> None:
        source = {
            "type": "regex",
            "url": "https://api.github.com/repos/openai/codex/releases/latest",
            "pattern": r'"tag_name"\s*:\s*"rust-v([^"]+)"',
        }

        with patch("agent_cli_audit.http_get_text", return_value='{"tag_name":"rust-v0.147.0"}'):
            self.assertEqual(get_latest_from_source(source), "0.147.0")

    def test_parses_cursor_agent_latest_from_official_installer(self) -> None:
        source = {
            "type": "regex",
            "url": "https://cursor.com/install",
            "pattern": r'FINAL_DIR="\$HOME/\.local/share/cursor-agent/versions/([^"]+)"',
        }

        installer = 'FINAL_DIR="$HOME/.local/share/cursor-agent/versions/2026.08.04-aaa8809"'
        with patch("agent_cli_audit.http_get_text", return_value=installer):
            self.assertEqual(get_latest_from_source(source), "2026.08.04-aaa8809")

    def test_cursor_agent_uses_native_script_update(self) -> None:
        guidance = upgrade_guidance({"id": "cursor-agent"}, "script", "recommended", None)

        self.assertEqual(guidance["kind"], "native_self_update")
        self.assertEqual(guidance["command"], "agent update")

    def test_requires_manual_review_for_codex_app_bundle(self) -> None:
        guidance = upgrade_guidance(
            {"id": "codex"}, "app-bundle", "nonstandard", "a stale migration command"
        )

        self.assertEqual(guidance["kind"], "app_bundle_update")
        self.assertIsNone(guidance["command"])

    def test_uses_migration_for_nonstandard_channel(self) -> None:
        guidance = upgrade_guidance(
            {"id": "amp"}, "unknown", "nonstandard", "curl -fsSL https://ampcode.com/install.sh | bash"
        )

        self.assertEqual(guidance["kind"], "migration")
        self.assertEqual(guidance["command"], "curl -fsSL https://ampcode.com/install.sh | bash")

    def test_uses_claude_update_not_upgrade(self) -> None:
        self.assertEqual(get_update_command({"id": "claude"}, "script"), "claude update")

    def test_keeps_package_manager_fallback_for_other_tools(self) -> None:
        command = get_update_command(
            {"id": "gemini", "npm_package": "@google/gemini-cli"}, "npm"
        )

        self.assertEqual(command, "mise exec node -- npm install -g @google/gemini-cli@latest")

    def test_grok_uses_native_update_for_script_installs(self) -> None:
        guidance = upgrade_guidance({"id": "grok"}, "script", "recommended", None)

        self.assertEqual(guidance["kind"], "native_self_update")
        self.assertEqual(guidance["command"], "grok update")

    def test_node_runtime_check_reports_missing_mise(self) -> None:
        with patch("agent_cli_audit.shutil.which", return_value=None):
            result = check_node_runtime()

        self.assertEqual(result["status"], "drift")
        self.assertIn("mise is not available on PATH", result["issues"])

    def test_bridges_macos_proxy_to_child_process_environment(self) -> None:
        with patch("agent_cli_audit.urllib.request.getproxies", return_value={"http": "http://127.0.0.1:7890"}):
            self.assertEqual(
                system_proxy_env(),
                {
                    "HTTP_PROXY": "http://127.0.0.1:7890",
                    "http_proxy": "http://127.0.0.1:7890",
                    "HTTPS_PROXY": "http://127.0.0.1:7890",
                    "https_proxy": "http://127.0.0.1:7890",
                },
            )

    def test_kiro_script_shim_is_distinct_from_its_app_bundle_container(self) -> None:
        path = str(Path.home() / ".local/bin/kiro-cli")
        resolved = "/Applications/Kiro CLI.app/Contents/MacOS/kiro-cli"

        self.assertEqual(detect_channel(path, resolved), "script")
        self.assertEqual(binary_container(path, resolved), "app-bundle")

    def test_chatgpt_bundled_codex_is_an_app_bundle_install(self) -> None:
        path = "/Applications/ChatGPT.app/Contents/Resources/codex"

        self.assertEqual(detect_channel(path, path), "app-bundle")
        self.assertEqual(binary_container(path, path), "app-bundle")

    def test_antigravity_uses_background_self_update_guidance(self) -> None:
        guidance = upgrade_guidance({"id": "antigravity"}, "script", "recommended", None)

        self.assertEqual(guidance["kind"], "background_self_update")
        self.assertIsNone(guidance["command"])

    def test_release_platform_matches_antigravity_manifest_names(self) -> None:
        with patch("agent_cli_audit.platform.system", return_value="Darwin"), patch(
            "agent_cli_audit.platform.machine", return_value="arm64"
        ):
            self.assertEqual(release_platform(), "darwin_arm64")

    def test_timed_out_command_does_not_leave_its_child_running(self) -> None:
        script = (
            "import subprocess, sys, time; "
            "child = subprocess.Popen(['sleep', '60']); "
            "print(child.pid, flush=True); "
            "time.sleep(60)"
        )

        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            run([sys.executable, "-c", script], timeout=0.2)

        child_pid = int((raised.exception.output or "").strip())
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("timed-out command left its child process running")


if __name__ == "__main__":
    unittest.main()
