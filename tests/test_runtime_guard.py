import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agent_cli_upgrade import main, npm_runtime_status


class NpmRuntimeGuardTests(unittest.TestCase):
    def test_skips_runtime_check_without_npm_plan_items(self) -> None:
        with patch("agent_cli_upgrade.check_node_runtime") as check:
            self.assertIsNone(npm_runtime_status([{"normalized_channel": "script"}]))

        check.assert_not_called()

    def test_returns_runtime_drift_for_npm_plan_items(self) -> None:
        expected = {"status": "drift", "issues": ["npm points outside mise"]}
        with patch("agent_cli_upgrade.check_node_runtime", return_value=expected):
            self.assertEqual(npm_runtime_status([{"normalized_channel": "npm"}]), expected)

    def test_apply_blocks_npm_upgrades_when_runtime_has_drift(self) -> None:
        item = {
            "id": "gemini",
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "normalized_channel": "npm",
            "channel_status": "recommended",
            "update_command": "mise exec node -- npm install -g @google/gemini-cli@latest",
        }
        drift = {"status": "drift", "issues": ["npm points outside mise"]}
        output = io.StringIO()
        with (
            patch("agent_cli_upgrade.load_audit", return_value=[item]),
            patch("agent_cli_upgrade.npm_runtime_status", return_value=drift),
            patch("agent_cli_upgrade.run_shell") as run_shell,
            patch("sys.argv", ["agent_cli_upgrade.py", "--apply", "--yes"]),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 1)

        run_shell.assert_not_called()
        self.assertIn("Refusing npm-channel upgrades", output.getvalue())

    def test_dry_run_reports_npm_runtime_drift_without_blocking(self) -> None:
        item = {
            "id": "gemini",
            "current_version": "1.0.0",
            "latest_version": "1.1.0",
            "normalized_channel": "npm",
            "channel_status": "recommended",
            "update_command": "mise exec node -- npm install -g @google/gemini-cli@latest",
        }
        drift = {"status": "drift", "issues": ["npm points outside mise"]}
        output = io.StringIO()
        with (
            patch("agent_cli_upgrade.load_audit", return_value=[item]),
            patch("agent_cli_upgrade.npm_runtime_status", return_value=drift),
            patch("sys.argv", ["agent_cli_upgrade.py", "--json"]),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)

        self.assertEqual(json.loads(output.getvalue())["runtime_drift"], drift)


if __name__ == "__main__":
    unittest.main()
