import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_cli_audit import (
    app_bundle_version,
    binary_container,
    check_node_runtime,
    configured_executables,
    detect_channel,
    get_latest_from_source,
    get_update_command,
    http_get_json,
    load_catalog,
    node_runtime_provider,
    npm_command_prefix,
    nori_local_agent_executables,
    private_harness_inventory,
    release_platform,
    run,
    select_catalog_records,
    source_checkout_details,
    system_proxy_env,
    upgrade_guidance,
)


class UpgradeGuidanceTests(unittest.TestCase):
    def test_json_http_lookup_replaces_malformed_utf8(self) -> None:
        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"version":"1.2.3\xcf"}'

        with patch("agent_cli_audit.urllib.request.urlopen", return_value=Response()):
            self.assertEqual(http_get_json("https://example.test"), {"version": "1.2.3\ufffd"})

    def test_catalog_separates_operations_from_runtime_adapters(self) -> None:
        classes = {record["id"]: record["tooling_class"] for record in load_catalog()}

        self.assertEqual(classes["codex-acp"], "tooling-runtime")
        self.assertEqual(classes["9router"], "agent-operations")
        self.assertEqual(classes["claude-code-router"], "agent-operations")
        self.assertEqual(classes["nmem"], "agent-operations")

    def test_catalog_class_filter_happens_before_probes(self) -> None:
        catalog = load_catalog()

        selected = select_catalog_records(catalog, set(), "agent-cli")

        self.assertEqual(len(selected), 18)
        self.assertNotIn("acpx", {record["id"] for record in selected})
        self.assertIn("grok", {record["id"] for record in selected})
        self.assertIn("jules", {record["id"] for record in selected})

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

    def test_parses_hermes_product_version_from_release_name(self) -> None:
        source = next(record["latest_source"] for record in load_catalog() if record["id"] == "hermes")
        payload = '{"tag_name":"v2026.8.19","name":"Hermes Agent v0.20.5 (v2026.8.19)"}'

        with patch("agent_cli_audit.http_get_text", return_value=payload):
            self.assertEqual(get_latest_from_source(source), "0.20.5")

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

    def test_node_runtime_check_reports_missing_node_and_npm(self) -> None:
        with patch("agent_cli_audit.shutil.which", return_value=None):
            result = check_node_runtime()

        self.assertEqual(result["status"], "drift")
        self.assertIn("node and npm must both be available on PATH", result["issues"])

    def test_recognizes_nvm_and_builds_a_path_bound_npm_command(self) -> None:
        node = "/Users/example/.nvm/versions/node/v22.14.0/bin/node"
        self.assertEqual(node_runtime_provider(node)["id"], "nvm")
        runtime = {
            "status": "pass",
            "executable": True,
            "runtime_provider": {"id": "nvm"},
            "runtime_paths": {
                "node": node,
                "npm": "/Users/example/.nvm/versions/node/v22.14.0/bin/npm",
            },
        }
        self.assertEqual(
            npm_command_prefix(runtime),
            "env PATH=/Users/example/.nvm/versions/node/v22.14.0/bin:$PATH /Users/example/.nvm/versions/node/v22.14.0/bin/npm",
        )

    def test_reports_source_checkout_distance_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "9router"
            (checkout / ".git").mkdir(parents=True)
            (checkout / "cli").mkdir()
            launcher = Path(temporary) / "9router-launcher"
            launcher.write_text(f'exec "$NODE_BIN" "{checkout}/cli/cli.js" "$@"\n')

            def output(args: list[str]) -> tuple[str | None, str | None]:
                if args[-2:] == ["rev-parse", "HEAD"]:
                    return "local", None
                if args[-1] == "upstream/master":
                    return "upstream", None
                if args[-2:] == ["status", "--porcelain"]:
                    return "", None
                if args[-2:] == ["--count", "HEAD...upstream/master"]:
                    return "2\t1", None
                return None, "unexpected command"

            with patch("agent_cli_audit.command_output", side_effect=output):
                details = source_checkout_details(
                    str(launcher),
                    {
                        "launcher_pattern": r'exec "\$NODE_BIN" "([^"]+)/cli/cli\.js"',
                        "upstream_remote": "upstream",
                        "upstream_branch": "master",
                    },
                )

        self.assertEqual(details["source_checkout_path"], str(checkout))
        self.assertEqual(details["source_ahead"], 2)
        self.assertEqual(details["source_behind"], 1)

    def test_reports_launcher_relative_checkout_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "claude-code-router"
            (checkout / ".git").mkdir(parents=True)
            cli = checkout / "dist/cli.js"
            cli.parent.mkdir()
            cli.touch()
            pnpm_home = Path(temporary) / "pnpm"
            pnpm_home.mkdir()
            launcher = pnpm_home / "ccr"
            relative_checkout = os.path.relpath(checkout, pnpm_home)
            launcher.write_text(f'exec node "$basedir/{relative_checkout}/dist/cli.js" "$@"\n')

            def output(args: list[str]) -> tuple[str | None, str | None]:
                if args[-2:] == ["rev-parse", "HEAD"]:
                    return "local", None
                if args[-1] == "upstream/main":
                    return "upstream", None
                if args[-2:] == ["status", "--porcelain"]:
                    return "", None
                if args[-2:] == ["--count", "HEAD...upstream/main"]:
                    return "9\t0", None
                return None, "unexpected command"

            with patch("agent_cli_audit.command_output", side_effect=output):
                details = source_checkout_details(
                    str(launcher),
                    {
                        "mode": "launcher-relative",
                        "launcher_pattern": r'\$basedir/([^\"]+)/dist/cli\.js',
                        "upstream_remote": "upstream",
                        "upstream_branch": "main",
                    },
                )

        self.assertEqual(Path(details["source_checkout_path"]).resolve(), checkout.resolve())
        self.assertEqual(details["source_ahead"], 9)
        self.assertEqual(details["source_behind"], 0)

    def test_reads_app_bundle_version_as_a_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Orca.app/Contents/Resources/bin/orca"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch("agent_cli_audit.command_output", return_value=("1.4.184\n", None)):
                self.assertEqual(app_bundle_version(str(executable)), "1.4.184")

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

    def test_command_output_replaces_invalid_utf8_from_a_cli(self) -> None:
        completed = run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xcf')"],
            timeout=5,
        )

        self.assertEqual(completed.stdout, "\ufffd")

    def test_private_inventory_extracts_only_zed_executable_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "settings.json"
            config.write_text(
                '{"agent_servers":{"private":{"command":"codex-acp --token should-not-appear"}}}'
            )

            self.assertEqual(configured_executables(config, "agent_servers"), ["codex-acp"])

    def test_private_inventory_extracts_only_nori_local_agent_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(
                '[agents.distribution.local]\ncommand = "acpx"\nargs = ["--token", "hidden"]\n'
            )

            self.assertEqual(nori_local_agent_executables(config), ["acpx"])

    def test_private_inventory_compares_external_baseline_without_writing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "Test.app"
            private_binary = bundle / "Contents/Resources/codex"
            private_binary.parent.mkdir(parents=True)
            private_binary.write_text("placeholder")
            private_binary.chmod(0o755)
            baseline = root / "inventory.json"
            baseline.write_text('{"private_harness_inventory":{"fingerprint":"old"}}')
            before = baseline.read_text()

            with patch("agent_cli_audit.PRIVATE_HARNESS_HOSTS", (("test", "Test", bundle, root / "config.json"),)), patch(
                "agent_cli_audit.DEFAULT_HARNESS_ENTRYPOINTS", ()
            ), patch("agent_cli_audit.Path.home", return_value=root), patch(
                "agent_cli_audit.app_bundle_metadata", return_value={"bundle_path": str(bundle)}
            ):
                inventory = private_harness_inventory(baseline)
            after = baseline.read_text()

        self.assertEqual(inventory["baseline"]["status"], "changed")
        self.assertEqual(inventory["private_installations"][0]["version_status"], "not-probed")
        self.assertEqual(inventory["governance_risks"], [])
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
