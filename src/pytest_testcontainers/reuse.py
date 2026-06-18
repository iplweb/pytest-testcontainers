"""Resolution of project name + worker id + reuse-name composition.

This module is read-only state plus pure functions. It does NOT call
``docker``, does NOT touch the filesystem at import time, and does NOT
register any pytest hooks. Side-effecting code paths live in the maker
layer.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from pathlib import Path

# CLI overrides arrive at parse time and live process-wide.
_cli_state_lock = threading.Lock()
_cli_project: str | None = None
_cli_reuse: bool | None = None  # tri-state: None=unset, True=force, False=force off

# One-shot fallback warning gate (see §8.3 / §8.1).
_fallback_warned: bool = False


# --------------------------------------------------------------------------
# CLI state setters — called from plugin.pytest_configure (or similar)
# --------------------------------------------------------------------------
def set_cli_project(name: str | None) -> None:
    global _cli_project
    with _cli_state_lock:
        _cli_project = name


def set_cli_reuse(value: bool | None) -> None:
    global _cli_reuse
    with _cli_state_lock:
        _cli_reuse = value


def reset_cli_state() -> None:
    """Used in tests to keep cross-test state from leaking."""
    global _cli_project, _cli_reuse, _cli_disabled, _fallback_warned
    with _cli_state_lock:
        _cli_project = None
        _cli_reuse = None
        _cli_disabled = None
        _fallback_warned = False


# --------------------------------------------------------------------------
# Reuse-mode decision
# --------------------------------------------------------------------------
def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_reuse_enabled() -> bool:
    """Return True if reuse mode is on. CLI > env > default(off)."""
    if _cli_reuse is not None:
        return _cli_reuse
    return _is_truthy(os.environ.get("PYTEST_TESTCONTAINERS_REUSE"))


def _is_falsy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"0", "false", "no", "off"}


# Module-level state for the --no-testcontainers CLI flag (CLI > env).
_cli_disabled: bool | None = None


def set_cli_disabled(value: bool | None) -> None:
    global _cli_disabled
    with _cli_state_lock:
        _cli_disabled = value


def is_plugin_disabled() -> bool:
    """Return True when the user opted out of plugin fixtures.

    Triggered by ``--no-testcontainers`` or ``PYTEST_TESTCONTAINERS=0``.
    Maker functions are unaffected — only the fixture stubs.
    """
    if _cli_disabled is True:
        return True
    return _is_falsy(os.environ.get("PYTEST_TESTCONTAINERS"))


def is_quiet() -> bool:
    return _is_truthy(os.environ.get("PYTEST_TESTCONTAINERS_QUIET"))


# --------------------------------------------------------------------------
# Worker id (xdist) — defaults to "master" when not under xdist
# --------------------------------------------------------------------------
def worker_id() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "master")


# --------------------------------------------------------------------------
# Project name resolution
# --------------------------------------------------------------------------
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


def _emit_fallback_warning() -> None:
    """One-shot stderr warning when the literal "pytest-tc" fallback is used."""
    global _fallback_warned
    if _fallback_warned or is_quiet():
        return
    _fallback_warned = True
    sys.stderr.write(
        "[pytest-testcontainers] using fallback project name 'pytest-tc' for\n"
        "reuse-name prefix (no [project].name found in pyproject.toml).\n"
        "Tip: set PYTEST_TESTCONTAINERS_PROJECT=<name> to avoid collisions\n"
        "with other projects on the same machine.\n"
        "Suppress this notice with PYTEST_TESTCONTAINERS_QUIET=1.\n"
    )


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


# --------------------------------------------------------------------------
# Container-name sanitization + composition
# --------------------------------------------------------------------------
_INVALID_CHAR = re.compile(r"[^a-z0-9_.-]")
_RUN_OF_DASH = re.compile(r"-+")
_LEADING_NON_ALNUM = re.compile(r"^[^a-z0-9]+")


def sanitize_component(component: str) -> str:
    """Make a single name component Docker-safe per §8.4."""
    s = component.lower()
    s = _INVALID_CHAR.sub("-", s)
    s = _RUN_OF_DASH.sub("-", s)
    s = _LEADING_NON_ALNUM.sub("", s)
    s = s.rstrip("-._")
    return s or "x"


def compose_name(project: str, service: str, worker: str) -> str:
    """Compose the full ``<project>-tc-<service>-<worker>`` reuse name.

    Truncates to 255 chars and appends a 4-char hash if the natural
    composition would overflow Docker's container-name limit.
    """
    p = sanitize_component(project)
    s = sanitize_component(service)
    w = sanitize_component(worker)
    name = f"{p}-tc-{s}-{w}"
    if len(name) <= 255:
        return name
    h = hashlib.blake2s(name.encode(), digest_size=2).hexdigest()
    return name[:250] + "-" + h


def reuse_name_for(service: str, *, override: str | None = None) -> str:
    """Compute the reuse name for ``service`` (already a short slug).

    If ``override`` is given (the user passed ``reuse_name=`` to a maker),
    it takes precedence verbatim — sanitized, but not composed.
    """
    if override is not None:
        return sanitize_component(override)
    return compose_name(project_name(), service, worker_id())
