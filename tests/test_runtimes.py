import unittest
from pathlib import Path

from src.runtime.runtimes import RuntimeRegistryError, get_runtime


class RuntimesTest(unittest.TestCase):
    def test_codex_runtime_is_registered(self) -> None:
        runtime = get_runtime("codex")
        self.assertEqual(
            runtime.startup_command,
            ["codex", "--dangerously-bypass-approvals-and-sandbox", "--no-alt-screen"],
        )
        self.assertEqual(runtime.ready_strategy.prompt_prefix, "› ")
        self.assertEqual(runtime.agent_logs_dir, Path.home() / ".codex" / "sessions")
        self.assertEqual(runtime.pre_paste_delay_seconds, 0.0)

    def test_claude_code_runtime_is_registered(self) -> None:
        runtime = get_runtime("claude-code")
        self.assertEqual(
            runtime.startup_command,
            ["claude", "--dangerously-skip-permissions"],
        )
        self.assertEqual(runtime.ready_strategy.prompt_prefix, "❯")
        self.assertEqual(runtime.ready_strategy.banner_substring, "Claude Code")
        self.assertEqual(runtime.ready_strategy.auto_respond_patterns, (("Yes, I trust this folder", ""),))
        self.assertEqual(runtime.model_flag, "--model")
        self.assertEqual(runtime.agent_logs_dir, Path.home() / ".claude" / "projects")
        self.assertEqual(runtime.pre_prompt_commands, ())
        self.assertEqual(runtime.pre_paste_delay_seconds, 5.0)

    def test_unknown_runtime_raises(self) -> None:
        with self.assertRaises(RuntimeRegistryError):
            get_runtime("missing")


if __name__ == "__main__":
    unittest.main()
