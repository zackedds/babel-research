from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import call, patch

from src.orchestration.worktree import create_worktree, ensure_branch, remove_worktree


class EnsureBranchTest(unittest.TestCase):
    def test_creates_branch_when_absent(self) -> None:
        completed_empty = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        completed_created = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch("src.orchestration.worktree.subprocess.run", side_effect=[completed_empty, completed_created]) as mock_run:
            ensure_branch(Path("/repo"), "feature/x")
        self.assertEqual(mock_run.call_count, 2)
        list_call, create_call = mock_run.call_args_list
        self.assertEqual(list_call.args[0], ["git", "branch", "--list", "feature/x"])
        self.assertEqual(create_call.args[0], ["git", "branch", "feature/x", "HEAD"])

    def test_skips_create_when_branch_exists(self) -> None:
        completed_found = subprocess.CompletedProcess([], 0, stdout=b"  feature/x\n", stderr=b"")
        with patch("src.orchestration.worktree.subprocess.run", return_value=completed_found) as mock_run:
            ensure_branch(Path("/repo"), "feature/x")
        self.assertEqual(mock_run.call_count, 1)


class CreateWorktreeTest(unittest.TestCase):
    def test_creates_sub_branch_and_worktree(self) -> None:
        ok = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch("src.orchestration.worktree.subprocess.run", return_value=ok) as mock_run:
            result = create_worktree(Path("/repo"), "abc123", "feature/x")

        self.assertEqual(result, Path("/repo/.worktrees/abc123"))
        branch_call, worktree_call = mock_run.call_args_list
        self.assertEqual(branch_call.args[0], ["git", "branch", "feature/x--abc123", "feature/x"])
        self.assertEqual(worktree_call.args[0], ["git", "worktree", "add", "/repo/.worktrees/abc123", "feature/x--abc123"])


class RemoveWorktreeTest(unittest.TestCase):
    def test_removes_worktree(self) -> None:
        ok = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch("src.orchestration.worktree.subprocess.run", return_value=ok) as mock_run:
            remove_worktree(Path("/repo"), "abc123")
        mock_run.assert_called_once_with(
            ["git", "worktree", "remove", "--force", ".worktrees/abc123"],
            capture_output=True,
            check=True,
            cwd=Path("/repo"),
        )

    def test_absorbs_exceptions(self) -> None:
        with patch("src.orchestration.worktree.subprocess.run", side_effect=RuntimeError("boom")):
            remove_worktree(Path("/repo"), "abc123")  # should not raise
