from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_branch(repo_root: Path, branch: str) -> None:
    """Create branch from HEAD if it doesn't exist."""
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        capture_output=True,
        check=True,
        cwd=repo_root,
    )
    if not result.stdout.strip():
        subprocess.run(
            ["git", "branch", branch, "HEAD"],
            capture_output=True,
            check=True,
            cwd=repo_root,
        )


def create_worktree(repo_root: Path, session_id: str, branch: str) -> Path:
    """Create sub-branch <branch>--<session_id> and a worktree for it."""
    sub_branch = f"{branch}--{session_id}"
    subprocess.run(
        ["git", "branch", sub_branch, branch],
        capture_output=True,
        check=True,
        cwd=repo_root,
    )
    worktree_path = repo_root / ".worktrees" / session_id
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), sub_branch],
        capture_output=True,
        check=True,
        cwd=repo_root,
    )
    return worktree_path


def remove_worktree(repo_root: Path, session_id: str) -> None:
    """Remove the worktree for session_id; absorbs all exceptions."""
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", f".worktrees/{session_id}"],
            capture_output=True,
            check=True,
            cwd=repo_root,
        )
    except Exception:
        pass
