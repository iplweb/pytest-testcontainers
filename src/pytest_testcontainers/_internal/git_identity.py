"""Filesystem-only git identity helpers: worktree root + current branch.

Pure reads — no subprocess, no docker, no import-time side effects. Used by
``reuse.py`` to compute the ``{branch}``, ``{dir}`` and ``{dirtag}`` name
components. Handles both a normal clone (``.git`` is a directory) and a
linked worktree (``.git`` is a file containing ``gitdir: <path>``).
"""

from __future__ import annotations

from pathlib import Path


def worktree_root(start: Path) -> Path:
    """Return the directory that owns the working tree containing ``start``.

    Walks upward from ``start`` looking for a ``.git`` entry (a directory in a
    normal clone, a file in a linked worktree — either marks the root). Falls
    back to ``start.resolve()`` when no ``.git`` is found.
    """
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _gitdir_for(root: Path) -> Path | None:
    """Resolve the gitdir that holds ``HEAD`` for the worktree at ``root``.

    Normal clone: ``<root>/.git`` is a directory → that directory.
    Linked worktree: ``<root>/.git`` is a file ``gitdir: <path>`` → ``<path>``
    (resolved relative to ``root`` when the recorded path is relative).
    Returns ``None`` when neither shape is readable.
    """
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            content = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if content.startswith(prefix):
            target = content[len(prefix) :].strip()
            if target:
                resolved = Path(target)
                if not resolved.is_absolute():
                    resolved = (root / resolved).resolve()
                return resolved
    return None


def current_branch(start: Path) -> str | None:
    """Return the current git branch name, or ``None``.

    Reads ``HEAD`` from the resolved gitdir without spawning git:
    - ``ref: refs/heads/<b>`` → ``<b>``
    - any other ``ref: .../<x>`` → last path component ``<x>``
    - raw SHA (detached HEAD) → first 12 chars
    - unreadable / missing / empty → ``None``
    """
    root = worktree_root(start)
    gitdir = _gitdir_for(root)
    if gitdir is None:
        return None
    try:
        content = (gitdir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not content:
        return None
    if content.startswith("ref:"):
        ref = content[len("ref:") :].strip()
        marker = "refs/heads/"
        if marker in ref:
            return ref.split(marker, 1)[1] or None
        return ref.rsplit("/", 1)[-1] or None
    return content[:12]
