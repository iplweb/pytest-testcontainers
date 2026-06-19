"""Filesystem-only git identity: worktree root + current branch (no subprocess)."""

from __future__ import annotations

from pathlib import Path

from pytest_testcontainers._internal.git_identity import current_branch, worktree_root


def _make_repo(root: Path, head: str) -> None:
    """Create a normal-clone .git dir with a HEAD file at ``root``."""
    git = root / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text(head, encoding="utf-8")


def test_worktree_root_is_dir_holding_dot_git(tmp_path: Path) -> None:
    _make_repo(tmp_path, "ref: refs/heads/main\n")
    sub = tmp_path / "pkg" / "deep"
    sub.mkdir(parents=True)
    assert worktree_root(sub) == tmp_path.resolve()


def test_worktree_root_no_repo_falls_back_to_start(tmp_path: Path) -> None:
    sub = tmp_path / "nope"
    sub.mkdir()
    assert worktree_root(sub) == sub.resolve()


def test_current_branch_attached_ref(tmp_path: Path) -> None:
    _make_repo(tmp_path, "ref: refs/heads/feature/x\n")
    assert current_branch(tmp_path) == "feature/x"


def test_current_branch_detached_head_short_sha(tmp_path: Path) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    _make_repo(tmp_path, sha + "\n")
    assert current_branch(tmp_path) == sha[:12]


def test_current_branch_no_repo_is_none(tmp_path: Path) -> None:
    assert current_branch(tmp_path) is None


def test_current_branch_linked_worktree_gitfile(tmp_path: Path) -> None:
    # Real gitdir lives elsewhere; the worktree's .git is a file pointing to it.
    real_gitdir = tmp_path / "realgit"
    real_gitdir.mkdir()
    (real_gitdir / "HEAD").write_text("ref: refs/heads/wt-branch\n", encoding="utf-8")

    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {real_gitdir.resolve()}\n", encoding="utf-8")

    assert worktree_root(wt) == wt.resolve()
    assert current_branch(wt) == "wt-branch"
