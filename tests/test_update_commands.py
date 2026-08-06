import unittest

from agent_cli_audit import get_update_command, upgrade_guidance


class UpgradeGuidanceTests(unittest.TestCase):
    def test_uses_channel_upgrade_when_native_ownership_is_unverified(self) -> None:
        record = {"id": "copilot", "npm_package": "@github/copilot"}

        guidance = upgrade_guidance(record, "npm", "recommended", None)

        self.assertEqual(guidance["command"], "npm install -g @github/copilot@latest")
        self.assertEqual(guidance["kind"], "channel_update")
        self.assertEqual(guidance["alternatives"][0]["command"], "copilot update")

    def test_uses_explicit_method_for_kilo_and_opencode(self) -> None:
        cases = {
            "kilocode": "kilo upgrade --method npm",
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

        self.assertEqual(command, "npm install -g @google/gemini-cli@latest")


if __name__ == "__main__":
    unittest.main()
