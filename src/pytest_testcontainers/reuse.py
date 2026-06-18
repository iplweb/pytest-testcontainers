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
import secrets
import sys
import threading
from pathlib import Path

from pytest_testcontainers._internal import git_identity
from pytest_testcontainers.errors import NameTemplateError

# CLI overrides arrive at parse time and live process-wide.
_cli_state_lock = threading.Lock()
_cli_project: str | None = None
_cli_reuse: bool | None = None  # tri-state: None=unset, True=force, False=force off
_cli_name_template: str | None = None

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


def set_cli_name_template(value: str | None) -> None:
    global _cli_name_template
    with _cli_state_lock:
        _cli_name_template = value


def reset_cli_state() -> None:
    """Used in tests to keep cross-test state from leaking."""
    global _cli_project, _cli_reuse, _cli_disabled, _fallback_warned, _cli_name_template
    with _cli_state_lock:
        _cli_project = None
        _cli_reuse = None
        _cli_disabled = None
        _fallback_warned = False
        _cli_name_template = None


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


DEFAULT_NAME_TEMPLATE = "{project}-tc-{service}-{branch}-{dirtag}-{worker}"
VALID_PLACEHOLDERS = frozenset({"project", "service", "branch", "dir", "dirtag", "worker", "rand"})
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


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
