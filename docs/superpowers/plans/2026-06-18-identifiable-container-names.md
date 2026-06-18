# Identifiable Container Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every container the plugin starts a readable, identifiable name derived from project + service + git branch + directory + worker, in both default and reuse mode, configurable via a template string.

**Architecture:** A new pure `_internal/git_identity.py` reads `.git/HEAD` (no subprocess) to derive branch and worktree root. `reuse.py` resolves a `{placeholder}` template (CLI > env > pyproject > default), renders it with all components, and runs the result through a single `finalize_name` length/charset guard. `makers.py` now names containers in *both* modes (find-or-create stays gated on reuse). `plugin.py` adds `--testcontainers-name-template`; `errors.py` adds `NameTemplateError`.

**Tech Stack:** Python 3.10–3.13, pytest 7/8, testcontainers-python ≥4.7, docker-py ≥6.1, `hashlib.blake2s`, `secrets`, `tomllib`/`tomli`. Managed with **uv**.

## Global Constraints

These apply to every task. Copied verbatim from the spec and CLAUDE.md:

- **Line length 100.** Ruff selects `E,F,W,I,B,UP,SIM,RUF`; `E501` is ignored (formatter wraps). Tests get `B011` exempted. After any source/test edit run `uv run ruff check --fix src/ tests/` and `uv run ruff format src/ tests/`.
- **`filterwarnings = ["error", ...]`** — any new warning fails the suite. Advisories MUST use the existing `sys.stderr.write(...)` pattern, never `warnings.warn`.
- **No silent exception swallowing.** Every `except` logs, re-raises, raises another, or returns a meaningful value. Narrow suppression needs a justifying comment.
- **Public symbols come out of `__init__.py` only.** Never re-export from `_internal/` except the already-public `DbConnInfo`.
- **Layering is strict:** `plugin → fixtures → makers → containers → _internal`. `errors.py` is a leaf (anything may import it). `reuse.py`/`makers.py` may import from `_internal`. Do NOT introduce upward edges.
- **Version floors:** Python 3.10, pytest 7, testcontainers ≥4.7, docker-py ≥6.1. Don't use newer-than-floor APIs.
- **Branch:** all work lands on `feature/identifiable-container-names` (already checked out). Commit messages end with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Docker container-name rules:** max 255 chars, charset `[a-zA-Z0-9][a-zA-Z0-9_.-]+`. Every assembled name passes through `finalize_name`.
- **Commands use uv:** `uv run pytest ...`, `uv run ruff ...`. Unit-only runs: `uv run pytest tests/ -m "not docker_required" -v`.

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/pytest_testcontainers/errors.py` | Exception hierarchy (leaf) | Add `NameTemplateError` |
| `src/pytest_testcontainers/__init__.py` | Public API surface | Export `NameTemplateError` |
| `src/pytest_testcontainers/_internal/git_identity.py` | Filesystem-only branch + worktree-root reads | **Create** |
| `src/pytest_testcontainers/reuse.py` | Project/template resolution, render, `finalize_name`, CLI state | Modify heavily |
| `src/pytest_testcontainers/makers.py` | Name containers in both modes | Modify `_make_generic` + import |
| `src/pytest_testcontainers/plugin.py` | `--testcontainers-name-template` flag + wiring | Modify |
| `tests/conftest.py` | Cross-test state reset | Add `PROJECT_NAME`, `COMPOSE_PROJECT_NAME`, `PYTEST_TESTCONTAINERS_NAME_TEMPLATE` to env sweep |
| `tests/test_git_identity.py` | Unit: branch/worktree detection | **Create** |
| `tests/test_reuse.py` | Unit: project precedence, template, finalize | Rewrite naming tests, add new |
| `tests/test_reuse_mode.py` | Reuse smoke | Update import only |
| `tests/test_makers_naming.py` | No-docker: maker names in both modes | **Create** |
| `tests/test_plugin_options.py` | Unit: CLI flag wires into reuse state | **Create** |
| `tests/test_naming_docker.py` | `docker_required` smoke: real name + per-branch identity | **Create** |
| `README.md`, `SPEC.md`, `CLAUDE.md` | Docs | Modify |

---

## Task 1: `NameTemplateError` exception

**Files:**
- Modify: `src/pytest_testcontainers/errors.py`
- Modify: `src/pytest_testcontainers/__init__.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `class NameTemplateError(PytestTestcontainersError)` — raised at name-resolution time for an unknown template placeholder; importable from both `pytest_testcontainers.errors` and the top-level `pytest_testcontainers` package.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_errors.py`:

```python
def test_name_template_error_is_plugin_error() -> None:
    from pytest_testcontainers import NameTemplateError
    from pytest_testcontainers.errors import NameTemplateError as ErrNameTemplateError
    from pytest_testcontainers.errors import PytestTestcontainersError

    assert NameTemplateError is ErrNameTemplateError
    assert issubclass(NameTemplateError, PytestTestcontainersError)
    with pytest.raises(PytestTestcontainersError):
        raise NameTemplateError("bad placeholder")
```

If `tests/test_errors.py` does not already `import pytest`, add it at the top.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_errors.py::test_name_template_error_is_plugin_error -v`
Expected: FAIL — `ImportError: cannot import name 'NameTemplateError'`.

- [ ] **Step 3: Add the exception class**

In `src/pytest_testcontainers/errors.py`, insert after the `ReuseConflictError` class (before `CleanSessionFixtureError`):

```python
class NameTemplateError(PytestTestcontainersError):
    """The configured container-name template referenced an unknown placeholder.

    Raised at name-resolution time (during fixture/maker setup, before any
    container starts). The message enumerates the valid placeholders.
    """
```

- [ ] **Step 4: Export it from the package**

In `src/pytest_testcontainers/__init__.py`, add `NameTemplateError` to the `from pytest_testcontainers.errors import (...)` block and to `__all__` (keep both alphabetical):

```python
from pytest_testcontainers.errors import (
    CleanSessionFixtureError,
    ContainerStartError,
    DockerNotRunningError,
    NameTemplateError,
    PytestTestcontainersError,
    ReuseConflictError,
)
```

and in `__all__` add `"NameTemplateError",` after `"DockerNotRunningError",`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_errors.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add src/pytest_testcontainers/errors.py src/pytest_testcontainers/__init__.py tests/test_errors.py
git commit -m "Add NameTemplateError exception

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `_internal/git_identity.py` — branch + worktree root

**Files:**
- Create: `src/pytest_testcontainers/_internal/git_identity.py`
- Test: `tests/test_git_identity.py`

**Interfaces:**
- Produces:
  - `worktree_root(start: Path) -> Path` — nearest ancestor (inclusive) holding a `.git` entry (dir *or* file); falls back to `start.resolve()` when no `.git` is found.
  - `current_branch(start: Path) -> str | None` — branch name from `HEAD`; detached HEAD → first 12 chars of the SHA; no repo / unreadable → `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_identity.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_git_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: ...git_identity`.

- [ ] **Step 3: Create the module**

Create `src/pytest_testcontainers/_internal/git_identity.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_git_identity.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add src/pytest_testcontainers/_internal/git_identity.py tests/test_git_identity.py
git commit -m "Add _internal/git_identity: branch + worktree-root reads

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Extend project-name precedence

Adds `PROJECT_NAME` / `COMPOSE_PROJECT_NAME` env vars and a cwd-basename fallback to the project-name chain, and factors the pyproject loader so Task 4 can reuse it. The literal `pytest-tc` + advisory now fires only when there is no cwd basename (i.e. cwd is the filesystem root) — so the two existing fallback tests change.

**Files:**
- Modify: `src/pytest_testcontainers/reuse.py` (`project_name`, new `_find_pyproject_data`, `_read_pyproject_project_name`, `_project_name_from_dir`)
- Modify: `tests/conftest.py` (env sweep)
- Test: `tests/test_reuse.py` (new precedence tests; update two fallback tests)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `project_name() -> str` — precedence: `_cli_project` > `PYTEST_TESTCONTAINERS_PROJECT` > `PROJECT_NAME` > `COMPOSE_PROJECT_NAME` > pyproject `[project].name` > cwd basename > literal `"pytest-tc"` (advisory only on the literal).
  - `_find_pyproject_data(start: Path) -> dict | None` — parsed nearest `pyproject.toml` walking up, or `None`.
  - `_project_name_from_dir(cwd: Path) -> str` — the file/basename/literal tail (testable without chdir to `/`).

- [ ] **Step 1: Extend the conftest env sweep**

In `tests/conftest.py`, add three vars to the `for var in (...)` tuple inside `_clean_state` (these are NOT `PYTEST_TESTCONTAINERS*`-prefixed, so the sweep otherwise misses them and they leak):

```python
    for var in (
        "PYTEST_TESTCONTAINERS",
        "PYTEST_TESTCONTAINERS_REUSE",
        "PYTEST_TESTCONTAINERS_PROJECT",
        "PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK",
        "PYTEST_TESTCONTAINERS_QUIET",
        "PYTEST_TESTCONTAINERS_NAME_TEMPLATE",
        "PROJECT_NAME",
        "COMPOSE_PROJECT_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
```

(`PYTEST_TESTCONTAINERS_NAME_TEMPLATE` is added now so Task 4 needs no further conftest change.)

- [ ] **Step 2: Write the failing tests**

In `tests/test_reuse.py`, **replace** the two existing fallback tests (`test_project_name_fallback_emits_warning` and `test_project_name_fallback_warning_suppressed_by_quiet`) with the block below, and add the new precedence tests. Add `from pathlib import Path` to the imports at the top of the file.

```python
def test_project_name_PROJECT_NAME_env(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("PROJECT_NAME", "from-project-name")
    assert project_name() == "from-project-name"


def test_project_name_COMPOSE_PROJECT_NAME_env(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "from-compose")
    assert project_name() == "from-compose"


def test_project_name_PROJECT_NAME_beats_compose(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("PROJECT_NAME", "wins")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "loses")
    assert project_name() == "wins"


def test_project_name_plugin_env_beats_PROJECT_NAME(
    monkeypatch: pytest.MonkeyPatch, tmp_cwd
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "plugin-env")
    monkeypatch.setenv("PROJECT_NAME", "generic-env")
    assert project_name() == "plugin-env"


def test_project_name_PROJECT_NAME_beats_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_cwd
) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "from-pyproject"
        """)
    )
    monkeypatch.setenv("PROJECT_NAME", "from-project-name")
    assert project_name() == "from-project-name"


def test_project_name_cwd_basename_fallback(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No pyproject, no env: fall back to the cwd directory basename, no warning.
    app = tmp_cwd / "myapp"
    app.mkdir()
    monkeypatch.chdir(app)
    assert project_name() == "myapp"
    assert capsys.readouterr().err == ""


def test_project_name_literal_fallback_emits_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Filesystem root has an empty basename → literal fallback + one-shot warning.
    assert reuse._project_name_from_dir(Path("/")) == "pytest-tc"
    assert "fallback project name" in capsys.readouterr().err
    # one-shot: second call is silent
    assert reuse._project_name_from_dir(Path("/")) == "pytest-tc"
    assert capsys.readouterr().err == ""


def test_project_name_literal_fallback_suppressed_by_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_QUIET", "1")
    assert reuse._project_name_from_dir(Path("/")) == "pytest-tc"
    assert capsys.readouterr().err == ""
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_reuse.py -k "project_name" -v`
Expected: FAIL — `_project_name_from_dir` does not exist; `PROJECT_NAME`/`COMPOSE_PROJECT_NAME` are ignored.

- [ ] **Step 4: Refactor the pyproject loader and project resolution**

In `src/pytest_testcontainers/reuse.py`, **replace** the entire `_read_pyproject_project_name` function with a factored loader plus a thin reader:

```python
def _find_pyproject_data(start: Path) -> dict | None:
    """Return parsed data of the nearest ``pyproject.toml`` walking up from ``start``.

    The first ``pyproject.toml`` found wins (even if it lacks the field a
    caller wants). Returns ``None`` when none is reachable or parsing fails.
    """
    try:
        if sys.version_info >= (3, 11):
            import tomllib  # type: ignore[import-not-found]
        else:  # pragma: no cover — exercised on 3.10 only
            import tomli as tomllib  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — tomli is a dep on 3.10
        return None

    current = start.resolve()
    for candidate in [current, *current.parents]:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            with pyproject.open("rb") as fh:
                return tomllib.load(fh)
        except (OSError, ValueError):
            return None
    return None


def _read_pyproject_project_name(start: Path) -> str | None:
    """Read ``[project].name`` from the nearest ``pyproject.toml``."""
    data = _find_pyproject_data(start)
    if data is None:
        return None
    project = data.get("project", {})
    name = project.get("name") if isinstance(project, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None
```

Then **replace** the `project_name` function with the extended chain plus the testable tail:

```python
def project_name() -> str:
    """Resolve the ``{project}`` component used in container names.

    Precedence (most explicit first): CLI override, the plugin's own
    ``PYTEST_TESTCONTAINERS_PROJECT``, the generic ``PROJECT_NAME`` then
    ``COMPOSE_PROJECT_NAME`` env vars, ``pyproject.toml [project].name``, the
    cwd basename, and finally the literal ``"pytest-tc"`` (which triggers the
    one-shot stderr advisory).
    """
    if _cli_project:
        return _cli_project
    env = os.environ.get("PYTEST_TESTCONTAINERS_PROJECT")
    if env:
        return env
    generic = os.environ.get("PROJECT_NAME") or os.environ.get("COMPOSE_PROJECT_NAME")
    if generic:
        return generic
    return _project_name_from_dir(Path.cwd())


def _project_name_from_dir(cwd: Path) -> str:
    """File/basename/literal tail of :func:`project_name` (separately testable)."""
    found = _read_pyproject_project_name(cwd)
    if found:
        return found
    basename = cwd.name
    if basename:
        return basename
    _emit_fallback_warning()
    return "pytest-tc"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_reuse.py -k "project_name" -v`
Expected: PASS (all project_name tests, including the new precedence + fallback ones).

- [ ] **Step 6: Run the full reuse + conftest sanity check**

Run: `uv run pytest tests/test_reuse.py -v`
Expected: the `compose_name` / `reuse_name_for` tests still PASS here (untouched until Task 4); project_name tests PASS.

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add src/pytest_testcontainers/reuse.py tests/conftest.py tests/test_reuse.py
git commit -m "Extend project-name precedence: PROJECT_NAME, COMPOSE_PROJECT_NAME, cwd basename

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Template machinery — render + finalize + `container_name_for`

Replaces `compose_name` / `reuse_name_for` with template rendering. Adds `name_template()`, `finalize_name()`, `container_name_for()`, the `{placeholder}` renderer, and the name-template CLI state. This is where the default template, both-mode `{rand}` handling, and the 255-char guard live.

**Files:**
- Modify: `src/pytest_testcontainers/reuse.py`
- Modify: `tests/test_reuse_mode.py` (import + call rename only)
- Test: `tests/test_reuse.py` (replace `compose_name`/`reuse_name_for` tests with template/finalize/`container_name_for` tests)

**Interfaces:**
- Consumes: `git_identity.worktree_root`, `git_identity.current_branch`, `errors.NameTemplateError`, `project_name`, `worker_id`, `sanitize_component`, `_find_pyproject_data` (Task 3).
- Produces:
  - `DEFAULT_NAME_TEMPLATE = "{project}-tc-{service}-{branch}-{dirtag}-{worker}"`
  - `VALID_PLACEHOLDERS: frozenset[str]` = `{project, service, branch, dir, dirtag, worker, rand}`
  - `name_template() -> str` — CLI > `PYTEST_TESTCONTAINERS_NAME_TEMPLATE` > pyproject `[tool.pytest_testcontainers].name_template` > `DEFAULT_NAME_TEMPLATE`.
  - `finalize_name(name: str) -> str` — sanitize whole string; if >255, `sanitized[:246] + "-" + blake2s(name, digest_size=4).hexdigest()`.
  - `container_name_for(service: str, *, override: str | None = None, reuse_mode: bool = True) -> str` — override wins (sanitized only); else render template + finalize. `{rand}` empty when `reuse_mode`, a fresh `secrets.token_hex(2)` otherwise (auto-appended when the template omits `{rand}`).
  - `set_cli_name_template(value: str | None) -> None`; `reset_cli_state()` also clears it.
  - Removed: `compose_name`, `reuse_name_for`.

- [ ] **Step 1: Update the reuse-smoke import (keep it green later)**

In `tests/test_reuse_mode.py` change the import and the call:

```python
from pytest_testcontainers.reuse import container_name_for
```

and inside `test_reuse_keeps_same_container_id`:

```python
    name = container_name_for("redis")
```

(`reuse_mode` defaults to `True`, so this matches the name the maker computes in reuse mode.)

- [ ] **Step 2: Write the failing unit tests**

In `tests/test_reuse.py`, **remove** `compose_name` from the import block and **delete** `test_compose_name_short`, `test_compose_name_truncates_when_long`, `test_reuse_name_for_uses_override`, `test_reuse_name_for_composes`, and `test_reuse_name_for_with_xdist_worker`. Update the import block to:

```python
from pytest_testcontainers import reuse
from pytest_testcontainers.errors import NameTemplateError
from pytest_testcontainers.reuse import (
    DEFAULT_NAME_TEMPLATE,
    container_name_for,
    finalize_name,
    is_plugin_disabled,
    is_reuse_enabled,
    name_template,
    project_name,
    sanitize_component,
    worker_id,
)
```

Then add:

```python
# --- template rendering -------------------------------------------------

def test_render_expands_all_placeholders() -> None:
    values = {
        "project": "myproj",
        "service": "psql",
        "branch": "feature-x",
        "dir": "checkout",
        "dirtag": "3f9ac1b2",
        "worker": "gw0",
        "rand": "ab12",
    }
    out = reuse._render_template(
        "{project}-tc-{service}-{branch}-{dir}-{dirtag}-{worker}-{rand}", values
    )
    assert out == "myproj-tc-psql-feature-x-checkout-3f9ac1b2-gw0-ab12"


def test_render_unknown_placeholder_raises() -> None:
    with pytest.raises(NameTemplateError) as exc:
        reuse._render_template("{project}-{nope}", {"project": "p"})
    assert "nope" in str(exc.value)
    assert "project" in str(exc.value)  # message lists valid placeholders


def test_finalize_collapses_empty_branch() -> None:
    # An empty {branch} leaves a double dash that finalize collapses cleanly.
    rendered = reuse._render_template(
        DEFAULT_NAME_TEMPLATE,
        {
            "project": "myproj",
            "service": "psql",
            "branch": "",
            "dir": "d",
            "dirtag": "3f9ac1b2",
            "worker": "master",
            "rand": "",
        },
    )
    assert finalize_name(rendered) == "myproj-tc-psql-3f9ac1b2-master"


# --- finalize_name length safety ---------------------------------------

def test_finalize_name_short_passthrough() -> None:
    assert finalize_name("MyProj-TC-Psql-Master") == "myproj-tc-psql-master"


def test_finalize_name_caps_at_255() -> None:
    out = finalize_name("x" * 400)
    assert len(out) <= 255


def test_finalize_name_deterministic() -> None:
    long = "y" * 400
    assert finalize_name(long) == finalize_name(long)


def test_finalize_name_distinguishes_tail() -> None:
    base = "z" * 300
    a = finalize_name(base + "-alpha")
    b = finalize_name(base + "-omega")
    assert a != b  # hash over the full name, not just the kept prefix


# --- name_template precedence ------------------------------------------

def test_name_template_default(tmp_cwd) -> None:
    assert name_template() == DEFAULT_NAME_TEMPLATE


def test_name_template_env_over_pyproject(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [tool.pytest_testcontainers]
            name_template = "{service}-from-pyproject"
        """)
    )
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{service}-from-env")
    assert name_template() == "{service}-from-env"


def test_name_template_pyproject(tmp_cwd) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [tool.pytest_testcontainers]
            name_template = "{service}-from-pyproject"
        """)
    )
    assert name_template() == "{service}-from-pyproject"


def test_name_template_cli_wins(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{service}-env")
    reuse.set_cli_name_template("{service}-cli")
    assert name_template() == "{service}-cli"


# --- container_name_for end to end -------------------------------------

def test_container_name_for_override(tmp_cwd) -> None:
    assert container_name_for("psql", override="MY_NAME") == "my_name"


def test_container_name_for_reuse_is_byte_stable(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{project}-tc-{service}-{worker}")
    monkeypatch.setenv("PROJECT_NAME", "myproj")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    first = container_name_for("psql", reuse_mode=True)
    second = container_name_for("psql", reuse_mode=True)
    assert first == second == "myproj-tc-psql-master"


def test_container_name_for_default_appends_rand(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{project}-tc-{service}-{worker}")
    monkeypatch.setenv("PROJECT_NAME", "myproj")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    name = container_name_for("psql", reuse_mode=False)
    assert re.match(r"^myproj-tc-psql-master-[0-9a-f]{4}$", name)


def test_container_name_for_explicit_rand_not_double_appended(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{service}-{rand}")
    name = container_name_for("psql", reuse_mode=False)
    assert re.match(r"^psql-[0-9a-f]{4}$", name)  # exactly one rand token
```

Add `import re` to the top of `tests/test_reuse.py` (alongside `textwrap`).

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_reuse.py -v`
Expected: FAIL — `ImportError` for `DEFAULT_NAME_TEMPLATE` / `container_name_for` / `finalize_name` / `name_template`.

- [ ] **Step 4: Add imports + CLI state to `reuse.py`**

At the top of `src/pytest_testcontainers/reuse.py`, add `import secrets` (keep imports sorted: it goes after `import re`). Add the cross-module imports after the existing stdlib imports:

```python
from pytest_testcontainers._internal import git_identity
from pytest_testcontainers.errors import NameTemplateError
```

Add the CLI state variable next to `_cli_project` (in the CLI-overrides block near the top):

```python
_cli_name_template: str | None = None
```

Add its setter next to `set_cli_project`:

```python
def set_cli_name_template(value: str | None) -> None:
    global _cli_name_template
    with _cli_state_lock:
        _cli_name_template = value
```

Update `reset_cli_state` to clear it:

```python
def reset_cli_state() -> None:
    """Used in tests to keep cross-test state from leaking."""
    global _cli_project, _cli_reuse, _cli_disabled, _fallback_warned, _cli_name_template
    with _cli_state_lock:
        _cli_project = None
        _cli_reuse = None
        _cli_disabled = None
        _fallback_warned = False
        _cli_name_template = None
```

- [ ] **Step 5: Add template constants, reader, render, finalize, and `container_name_for`**

In `src/pytest_testcontainers/reuse.py`, **replace** the whole "Container-name sanitization + composition" section — i.e. `compose_name` and `reuse_name_for` (keep `sanitize_component` and the `_INVALID_CHAR` / `_RUN_OF_DASH` / `_LEADING_NON_ALNUM` regexes) — with:

```python
DEFAULT_NAME_TEMPLATE = "{project}-tc-{service}-{branch}-{dirtag}-{worker}"
VALID_PLACEHOLDERS = frozenset(
    {"project", "service", "branch", "dir", "dirtag", "worker", "rand"}
)
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def _read_pyproject_name_template(start: Path) -> str | None:
    """Read ``[tool.pytest_testcontainers].name_template`` from pyproject."""
    data = _find_pyproject_data(start)
    if data is None:
        return None
    tool = data.get("tool", {})
    table = tool.get("pytest_testcontainers", {}) if isinstance(tool, dict) else {}
    template = table.get("name_template") if isinstance(table, dict) else None
    if isinstance(template, str) and template.strip():
        return template.strip()
    return None


def name_template() -> str:
    """Resolve the container-name template (CLI > env > pyproject > default)."""
    if _cli_name_template:
        return _cli_name_template
    env = os.environ.get("PYTEST_TESTCONTAINERS_NAME_TEMPLATE")
    if env:
        return env
    found = _read_pyproject_name_template(Path.cwd())
    if found:
        return found
    return DEFAULT_NAME_TEMPLATE


def _render_template(template: str, values: dict[str, str]) -> str:
    """Substitute ``{placeholder}`` tokens; unknown placeholder → error."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            valid = ", ".join("{" + p + "}" for p in sorted(VALID_PLACEHOLDERS))
            raise NameTemplateError(
                f"unknown placeholder {{{key}}} in container name template; "
                f"valid placeholders are: {valid}"
            )
        return values[key]

    return _PLACEHOLDER_RE.sub(_replace, template)


def _build_values(service: str, *, rand: str) -> dict[str, str]:
    """Resolve every placeholder value for the current cwd / git state."""
    cwd = Path.cwd()
    root = git_identity.worktree_root(cwd)
    dirtag = hashlib.blake2s(str(root).encode(), digest_size=4).hexdigest()
    return {
        "project": project_name(),
        "service": service,
        "branch": git_identity.current_branch(cwd) or "",
        "dir": root.name,
        "dirtag": dirtag,
        "worker": worker_id(),
        "rand": rand,
    }


def finalize_name(name: str) -> str:
    """Make ``name`` Docker-safe and ≤255 chars (deterministic trim+checksum)."""
    sanitized = sanitize_component(name)
    if len(sanitized) <= 255:
        return sanitized
    digest = hashlib.blake2s(name.encode(), digest_size=4).hexdigest()
    return sanitized[:246] + "-" + digest


def container_name_for(
    service: str, *, override: str | None = None, reuse_mode: bool = True
) -> str:
    """Compute the container name for ``service``.

    ``override`` (the maker ``reuse_name=`` argument) wins verbatim, sanitized
    but not templated. Otherwise the configured template is rendered and
    finalized. In reuse mode ``{rand}`` is empty so the name is byte-stable for
    find-or-create; in default mode a fresh ``secrets.token_hex(2)`` is used
    (auto-appended when the template does not already contain ``{rand}``) so
    overlapping or leaked runs never collide.
    """
    if override is not None:
        return sanitize_component(override)
    template = name_template()
    rand = "" if reuse_mode else secrets.token_hex(2)
    rendered = _render_template(template, _build_values(service, rand=rand))
    if not reuse_mode and "{rand}" not in template:
        rendered = f"{rendered}-{rand}"
    return finalize_name(rendered)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_reuse.py -v`
Expected: PASS (all template / finalize / precedence / `container_name_for` tests).

- [ ] **Step 7: Confirm nothing else imports the removed names**

Run: `grep -rn "compose_name\|reuse_name_for" src/ tests/`
Expected: only `src/pytest_testcontainers/makers.py` still references `reuse_name_for` (fixed in Task 5). If anything else appears, it must be updated before continuing.

- [ ] **Step 8: Lint + commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add src/pytest_testcontainers/reuse.py tests/test_reuse.py tests/test_reuse_mode.py
git commit -m "Add template-based container naming: render, finalize_name, container_name_for

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Name containers in both modes (`makers.py`)

`_make_generic` now computes a name unconditionally and applies it via `with_name` in default mode too. Find-or-create and Ryuk-disable stay gated on `reuse_on`; the atexit safety net and teardown stop stay gated on `not reuse_on`.

**Files:**
- Modify: `src/pytest_testcontainers/makers.py`
- Test: `tests/test_makers_naming.py` (create)

**Interfaces:**
- Consumes: `reuse.container_name_for` (Task 4), `reuse.is_reuse_enabled`, `reuse.sanitize_component`.
- Produces: every maker-started container is named; default-mode names carry a `{rand}` suffix, reuse-mode names are deterministic.

- [ ] **Step 1: Write the failing no-docker tests**

Create `tests/test_makers_naming.py`:

```python
"""Maker naming with the daemon ping and start mocked out (no real Docker)."""

from __future__ import annotations

import re

import pytest

from pytest_testcontainers import makers
from pytest_testcontainers.makers import make_container
from pytest_testcontainers.reuse import container_name_for


class _Fake:
    def __init__(self, *args, **kwargs) -> None:
        self.assigned_name: str | None = None

    def with_name(self, name):
        self.assigned_name = name
        return self

    def with_env(self, *args, **kwargs):
        return self

    def stop(self):
        pass


@pytest.fixture
def _stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(makers, "check_docker_daemon", lambda: None)
    monkeypatch.setattr(makers, "start_or_raise", lambda instance, image: None)


def test_default_mode_assigns_templated_name(
    _stub_runtime, tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    captured: dict[str, str] = {}

    class Fake(_Fake):
        def with_name(self, name):
            captured["name"] = name
            return super().with_name(name)

    with make_container(Fake):
        pass

    name = captured["name"]
    assert "-tc-fake-" in name  # service slug derived from the class name
    assert re.search(r"-[0-9a-f]{4}$", name)  # default mode appends a rand token


def test_reuse_mode_assigns_deterministic_name(
    _stub_runtime, tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")
    monkeypatch.setattr(makers, "find_existing_container", lambda name: None)
    monkeypatch.setattr(makers, "disable_ryuk_once", lambda: None)
    captured: dict[str, str] = {}

    class Fake(_Fake):
        def with_name(self, name):
            captured["name"] = name
            return super().with_name(name)

    expected = container_name_for("fake", reuse_mode=True)
    with make_container(Fake):
        pass

    assert captured["name"] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_makers_naming.py -v`
Expected: FAIL — `ImportError: cannot import name 'container_name_for'` is already fixed, but the maker still imports `reuse_name_for`, so `make_container` raises `ImportError` at import / the default-mode container is never named.

- [ ] **Step 3: Update the maker import**

In `src/pytest_testcontainers/makers.py`, change the `from pytest_testcontainers.reuse import (...)` block:

```python
from pytest_testcontainers.reuse import (
    container_name_for,
    is_reuse_enabled,
    sanitize_component,
)
```

- [ ] **Step 4: Name in both modes inside `_make_generic`**

In `_make_generic`, replace the reuse decision + name line:

```python
    reuse_on = is_reuse_enabled() or reuse_name is not None
    name = container_name_for(service_slug, override=reuse_name, reuse_mode=reuse_on)
```

Change the find-or-create guard from `if reuse_on and name is not None:` to:

```python
    if reuse_on:
```

In the fresh-start branch, drop the `if name is not None:` guard so default mode is named too:

```python
    if instance is None:
        instance = container_cls(*constructor_args, **constructor_kwargs)
        instance.with_name(name)
        _apply_env(instance, env)

        image_for_msg = constructor_kwargs.get("image") or (
            constructor_args[0] if constructor_args else container_cls.__name__
        )
        start_or_raise(instance, str(image_for_msg))

        if not reuse_on:
            register_atexit_stop(instance)
```

(The teardown block is unchanged — it already stops only when `not (reuse_on or is_bound_to_existing)`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_makers_naming.py tests/test_makers_no_docker.py -v`
Expected: PASS (new naming tests + the existing daemon-not-running test still green).

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add src/pytest_testcontainers/makers.py tests/test_makers_naming.py
git commit -m "Name containers in both default and reuse mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `--testcontainers-name-template` CLI flag

**Files:**
- Modify: `src/pytest_testcontainers/plugin.py`
- Test: `tests/test_plugin_options.py` (create)

**Interfaces:**
- Consumes: `reuse.set_cli_name_template`, `reuse.name_template` (Task 4).
- Produces: `--testcontainers-name-template=<tpl>` sets the highest-precedence template via `reuse.set_cli_name_template` in `pytest_configure`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_options.py`:

```python
"""Plugin CLI option wiring (no pytester — exercise the hooks directly)."""

from __future__ import annotations

from pytest_testcontainers import plugin, reuse


class _StubConfig:
    """Minimal duck-typed pytest.Config exposing getoption()."""

    def __init__(self, options: dict[str, object]) -> None:
        self._options = options

    def getoption(self, name: str):
        return self._options.get(name)


def _base_options(**overrides: object) -> dict[str, object]:
    opts: dict[str, object] = {
        "testcontainers_disabled": False,
        "testcontainers_project": None,
        "testcontainers_no_reuse": False,
        "testcontainers_reuse": False,
        "testcontainers_name_template": None,
    }
    opts.update(overrides)
    return opts


def test_name_template_flag_wires_into_reuse_state() -> None:
    config = _StubConfig(_base_options(testcontainers_name_template="{service}-from-cli"))
    plugin.pytest_configure(config)
    assert reuse.name_template() == "{service}-from-cli"


def test_no_name_template_flag_leaves_default(tmp_cwd) -> None:
    config = _StubConfig(_base_options())
    plugin.pytest_configure(config)
    assert reuse.name_template() == reuse.DEFAULT_NAME_TEMPLATE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugin_options.py -v`
Expected: FAIL — `getoption("testcontainers_name_template")` returns `None`/unhandled and `name_template()` is not set (first test fails on the assertion).

- [ ] **Step 3: Register the option**

In `src/pytest_testcontainers/plugin.py`, inside `pytest_addoption`, add after the `--testcontainers-project` option:

```python
    group.addoption(
        "--testcontainers-name-template",
        action="store",
        default=None,
        dest="testcontainers_name_template",
        help=(
            "Template for container names. Placeholders: {project} {service} "
            "{branch} {dir} {dirtag} {worker} {rand}."
        ),
    )
```

- [ ] **Step 4: Wire it in `pytest_configure`**

In `pytest_configure`, add after the `testcontainers_project` handling:

```python
    name_template = config.getoption("testcontainers_name_template")
    if name_template:
        reuse.set_cli_name_template(name_template)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_plugin_options.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add src/pytest_testcontainers/plugin.py tests/test_plugin_options.py
git commit -m "Add --testcontainers-name-template CLI flag

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Docker smoke tests (real names + per-branch identity)

**Files:**
- Test: `tests/test_naming_docker.py` (create)

**Interfaces:**
- Consumes: `make_redis`, `reuse.container_name_for`, docker-py. Marked `@pytest.mark.docker_required` (auto-skipped without a daemon).

- [ ] **Step 1: Write the smoke tests**

Create `tests/test_naming_docker.py`:

```python
"""docker_required: a default-mode container really carries the templated name,
and reuse identity differs across git branches."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_redis
from pytest_testcontainers.reuse import container_name_for

pytestmark = pytest.mark.docker_required


def _remove_named(name: str) -> None:
    import docker
    from docker.errors import NotFound

    client = docker.from_env()
    try:
        client.containers.get(name).remove(force=True)
    except NotFound:
        return


def test_default_mode_container_has_templated_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "ptc-name-smoke")
    with make_redis() as r:
        docker_name = r._container.name
        assert docker_name.startswith("ptc-name-smoke-tc-redis-")


def test_reuse_identity_differs_across_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    # The {branch} component is part of the reuse identity, so two branches
    # resolve to two distinct container names.
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "ptc-branch-smoke")
    monkeypatch.setenv(
        "PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{project}-tc-{service}-{branch}"
    )
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")

    monkeypatch.setattr(
        "pytest_testcontainers.reuse.git_identity.current_branch", lambda start: "branch-a"
    )
    name_a = container_name_for("redis")

    monkeypatch.setattr(
        "pytest_testcontainers.reuse.git_identity.current_branch", lambda start: "branch-b"
    )
    name_b = container_name_for("redis")

    assert name_a != name_b
    assert name_a == "ptc-branch-smoke-tc-redis-branch-a"
    assert name_b == "ptc-branch-smoke-tc-redis-branch-b"

    _remove_named("ptc-name-smoke-tc-redis")  # defensive: no leak from sibling test
```

Note: the second test is pure-resolution and would pass without Docker, but it is grouped here under `docker_required` because it documents reuse-identity behavior alongside the real-container smoke. Keep it in this file for cohesion.

- [ ] **Step 2: Run the smoke tests (skips cleanly without Docker)**

Run: `uv run pytest tests/test_naming_docker.py -v`
Expected: PASS when a Docker daemon is reachable; SKIPPED otherwise (conftest auto-skip). Both outcomes are acceptable for this step.

- [ ] **Step 3: Commit**

```bash
uv run ruff check --fix src/ tests/ && uv run ruff format src/ tests/
git add tests/test_naming_docker.py
git commit -m "Add docker_required naming smoke tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Full suite + docs

Run the whole unit suite green, then document the feature.

**Files:**
- Modify: `README.md`, `SPEC.md`, `CLAUDE.md`

- [ ] **Step 1: Run the full unit suite + lint gate (must be green before docs)**

```bash
uv run pytest tests/ -m "not docker_required" -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: all unit tests PASS; ruff reports no issues. Fix anything red before continuing.

- [ ] **Step 2: README — Configuration env-var table**

In `README.md`, in the `### Environment variables` table (around line 428), add three rows after the `PYTEST_TESTCONTAINERS_PROJECT` row:

```markdown
| `PROJECT_NAME=<name>`                     | Generic project-name source (below the plugin var). |
| `COMPOSE_PROJECT_NAME=<name>`             | docker-compose's project var (below `PROJECT_NAME`). |
| `PYTEST_TESTCONTAINERS_NAME_TEMPLATE=<t>` | Override the container-name template.       |
```

- [ ] **Step 3: README — Configuration CLI-flags table**

In the `### CLI flags` table (around line 438), add a row after `--testcontainers-project=NAME`:

```markdown
| `--testcontainers-name-template=T` | `PYTEST_TESTCONTAINERS_NAME_TEMPLATE=T` |
```

- [ ] **Step 4: README — new "Container names" subsection**

Insert a new subsection in `README.md` immediately before `## Reuse mode` (line 386):

````markdown
## Container names

Every container the plugin starts is named so you can find it in `docker ps`
(previously default-mode containers got random Docker names like
`heuristic_cannon`). The name is built from a template:

```
{project}-tc-{service}-{branch}-{dirtag}-{worker}
```

e.g. `myproj-tc-psql-feature-x-3f9ac1b2-master`.

Placeholders:

| Placeholder | Meaning |
|-------------|---------|
| `{project}` | Project name (see precedence below). |
| `{service}` | Service slug (`psql`, `redis`, `mysql`, `mongo`, `rabbitmq`, or derived from the container class). |
| `{branch}`  | Current git branch (read from `.git/HEAD`, no subprocess). Detached HEAD → 12-char commit SHA. No repo → empty. |
| `{dir}`     | Worktree-root basename. Readable, not collision-proof. |
| `{dirtag}`  | 8-hex hash of the worktree-root absolute path. Disambiguates the same branch checked out in two directories. |
| `{worker}`  | xdist worker id (`master` when not under xdist). |
| `{rand}`    | 4 random hex chars. Empty in reuse mode; auto-appended in default mode when the template omits it. |

Empty placeholders collapse cleanly (no `--`, no dangling dashes). Names are
sanitized to Docker's charset and capped at 255 chars (trim + checksum).

Override the template (CLI > env > pyproject > built-in default):

```bash
pytest --testcontainers-name-template='{project}-{service}-{branch}'
# or
export PYTEST_TESTCONTAINERS_NAME_TEMPLATE='{project}-{service}-{branch}'
```

```toml
# pyproject.toml
[tool.pytest_testcontainers]
name_template = "{project}-{service}-{branch}"
```

The `{project}` component resolves as: `--testcontainers-project` →
`PYTEST_TESTCONTAINERS_PROJECT` → `PROJECT_NAME` → `COMPOSE_PROJECT_NAME` →
`pyproject.toml [project].name` → cwd basename.

An unknown placeholder raises `NameTemplateError` listing the valid ones.

````

- [ ] **Step 5: README — Reuse mode per-branch note + migration**

In `README.md` `## Reuse mode`, replace the first bullet under "What changes:" (currently the `<project>-tc-<service>-<worker>` line, ~line 398-400) with:

```markdown
- Each container gets a stable, identifiable name from the template
  (default `{project}-tc-{service}-{branch}-{dirtag}-{worker}`), so reuse is
  scoped per project **and** per git branch **and** per directory. Switching
  branch or worktree yields a *new* container; the previous one lingers
  (stopped) until you run `pytest --testcontainers-clean`.
```

Then add this note at the end of the `## Reuse mode` section (after the concurrent-invocations paragraph, ~line 420):

```markdown
> **Upgrading from 0.1.0:** reuse-mode names now encode branch + directory, so
> containers reused from an older version are not found by name — a fresh one
> is created and the old one lingers until `pytest --testcontainers-clean`.
```

- [ ] **Step 6: SPEC.md — env + CLI tables**

In `SPEC.md` `### 8.1 Environment variables` table (line 969), add after the `PYTEST_TESTCONTAINERS_PROJECT` row:

```markdown
| `PROJECT_NAME` | Generic project-name source, below `PYTEST_TESTCONTAINERS_PROJECT` and above `COMPOSE_PROJECT_NAME` (§8.3). |
| `COMPOSE_PROJECT_NAME` | docker-compose's standard project var, below `PROJECT_NAME` (§8.3). |
| `PYTEST_TESTCONTAINERS_NAME_TEMPLATE` | Override the container-name template (§8.7). |
```

In `### 8.2 CLI flags` (line 979), add after the `--testcontainers-project=NAME` row:

```markdown
| `--testcontainers-name-template=T` | Override the container-name template (§8.7). Highest precedence. |
```

- [ ] **Step 7: SPEC.md — amend §8.3 and add §8.7**

In `SPEC.md` `### 8.3`, replace the resolution list (items 1–4 at lines 996–1001) with:

```markdown
1. CLI `--testcontainers-project=NAME` if given.
2. Env `PYTEST_TESTCONTAINERS_PROJECT` if set.
3. Env `PROJECT_NAME` if set.
4. Env `COMPOSE_PROJECT_NAME` if set.
5. `pyproject.toml [project].name` from the nearest `pyproject.toml`
   walking up from `Path.cwd()` (`tomllib` ≥3.11; `tomli` on 3.10).
6. The cwd directory basename.
7. Fallback literal `"pytest-tc"` (only when the cwd basename is empty,
   i.e. the filesystem root) — this is what triggers the one-shot advisory.
```

Then insert a new subsection `### 8.7` immediately before `## 9. Error handling` (line 1214):

```markdown
### 8.7 Container name template

Every container the plugin starts — in **both** default and reuse mode — is
named by expanding a template and passing the result through `finalize_name`
(§8.4 sanitization + 255-char trim/checksum).

Default template: `{project}-tc-{service}-{branch}-{dirtag}-{worker}`.

Placeholders: `{project}` (§8.3), `{service}` (slug), `{branch}`
(`.git/HEAD`, no subprocess; detached → 12-char SHA; no repo → empty),
`{dir}` (worktree-root basename), `{dirtag}` (8-hex blake2s of the
worktree-root absolute path), `{worker}` (xdist id), `{rand}`
(`secrets.token_hex(2)` — empty in reuse mode for byte-stable find-or-create;
in default mode auto-appended when the template omits it). Empty placeholders
collapse with no dangling dashes. An unknown placeholder raises
`NameTemplateError`.

Template precedence: `--testcontainers-name-template` >
`PYTEST_TESTCONTAINERS_NAME_TEMPLATE` >
`pyproject.toml [tool.pytest_testcontainers].name_template` > built-in default.

Reuse-mode names are now per-(project, service, branch, directory, worker);
switching branch/worktree creates a new container and leaves the old one until
`--testcontainers-clean`. The maker `reuse_name=` argument still overrides the
whole scheme (sanitized verbatim, not templated).
```

- [ ] **Step 8: CLAUDE.md — configuration-precedence table**

In `CLAUDE.md`, in the "Configuration precedence" table, add rows:

```markdown
| `PROJECT_NAME=NAME`                        | —                                 |
| `COMPOSE_PROJECT_NAME=NAME`                | —                                 |
| `PYTEST_TESTCONTAINERS_NAME_TEMPLATE=TPL`  | `--testcontainers-name-template=TPL` |
```

- [ ] **Step 9: Final full-suite + lint gate**

```bash
uv run pytest tests/ -m "not docker_required" -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add README.md SPEC.md CLAUDE.md
git commit -m "Document identifiable container names

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** name in both modes (Task 5); `(project, service, branch, dir, dirtag, worker, rand)` (Tasks 2+4); template + precedence (Tasks 4+6); `PROJECT_NAME`/`COMPOSE_PROJECT_NAME` (Task 3); `finalize_name` 255-cap with full-name checksum (Task 4); `NameTemplateError` (Task 1); conftest env-leak fix (Task 3); reuse migration note (Task 8). All spec sections map to a task.
- **Resolved spec ambiguity (documented decision):** the spec lists "cwd basename (triggers the one-shot advisory)" but also says the advisory fires "only … the literal `pytest-tc` … when there is no cwd basename." This plan implements the latter: cwd basename is returned **silently**; the literal `pytest-tc` + advisory fires only when the basename is empty (filesystem root). This keeps the advisory meaningful (it warns about the generic shared name, not about a perfectly good cwd basename) and is unit-tested via `_project_name_from_dir(Path("/"))`.
- **Type/name consistency:** `container_name_for` (not `reuse_name_for`) is the single naming entry, imported by `makers.py`, `tests/test_reuse.py`, `tests/test_reuse_mode.py`. `compose_name`/`reuse_name_for` are fully removed (Task 4 Step 7 greps to confirm). `finalize_name`, `name_template`, `DEFAULT_NAME_TEMPLATE`, `VALID_PLACEHOLDERS` are the public module names used in tests.
