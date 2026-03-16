from pathlib import Path
import os
import shutil
import time
import unittest

from src.runtime.runtimes import get_runtime
from src.runtime.tmux import TmuxClient


@unittest.skipUnless(
    os.environ.get("BABEL_RUN_CODEX_TESTS") == "1",
    "set BABEL_RUN_CODEX_TESTS=1 to run tmux/Codex integration tests",
)
class TmuxIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("tmux") is None or shutil.which("codex") is None:
            self.skipTest("tmux and codex are required")
        self.tmux = TmuxClient()
        self.session_name = f"orch-test-{int(time.time() * 1000)}"

    def tearDown(self) -> None:
        self.tmux.kill_session(self.session_name)

    def test_codex_ready_and_paste_submit(self) -> None:
        runtime = get_runtime("codex")
        self.tmux.create_session(self.session_name, Path.cwd(), runtime.startup_command)
        self.tmux.wait_until_ready(self.session_name, runtime.ready_strategy)
        self.tmux.paste_and_submit(self.session_name, "Planner prompt\n\nSession prompt")
        pane = self.tmux.capture_pane(self.session_name, start=-40)

        self.assertIn("OpenAI Codex", pane)
        self.assertIn("Planner prompt", pane)


if __name__ == "__main__":
    unittest.main()
