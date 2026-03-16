import unittest

from src.runtime.tmux import SESSION_COMPLETED_MARKER, SESSION_FAILED_MARKER, TmuxClient


class TmuxClientOutcomeParsingTest(unittest.TestCase):
    def test_terminal_outcome_requires_standalone_marker_line(self) -> None:
        pane = (
            "Terminal completion contract:\n"
            f"- Print exactly `{SESSION_COMPLETED_MARKER}` as a standalone line only after the assigned work is finished.\n"
            f"- If blocked, print `{SESSION_FAILED_MARKER}` before stopping.\n"
        )

        self.assertIsNone(TmuxClient.terminal_outcome_from_pane(pane))

    def test_terminal_outcome_uses_last_standalone_marker_line(self) -> None:
        pane = (
            f"{SESSION_COMPLETED_MARKER}\n"
            "retrying after verification failed\n"
            f"{SESSION_FAILED_MARKER}\n"
        )

        self.assertEqual(TmuxClient.terminal_outcome_from_pane(pane), "failed")


if __name__ == "__main__":
    unittest.main()
