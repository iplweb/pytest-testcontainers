"""Pytest plugin entry point.

Registered via ``pyproject.toml`` ``[project.entry-points.pytest11]``.

Per §3.1 we keep this layer extremely thin: register CLI flags, copy
their values into module-level state in :mod:`pytest_testcontainers.reuse`,
expose the fixtures from :mod:`pytest_testcontainers.fixtures`. No
``pytest_load_initial_conftests`` hook, no env-var injection.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from pytest_testcontainers import reuse
from pytest_testcontainers.fixtures import *  # noqa: F403

if TYPE_CHECKING:
    import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("testcontainers", "pytest-testcontainers options")
    group.addoption(
        "--no-testcontainers",
        action="store_true",
        default=False,
        dest="testcontainers_disabled",
        help="Disable plugin fixtures (PYTEST_TESTCONTAINERS=0 equivalent).",
    )
    group.addoption(
        "--testcontainers-reuse",
        action="store_true",
        default=False,
        dest="testcontainers_reuse",
        help="Reuse named containers across runs (Ryuk disabled).",
    )
    group.addoption(
        "--testcontainers-no-reuse",
        action="store_true",
        default=False,
        dest="testcontainers_no_reuse",
        help="Force fresh-each-run mode regardless of env / config.",
    )
    group.addoption(
        "--testcontainers-project",
        action="store",
        default=None,
        dest="testcontainers_project",
        help="Override project name used in reuse-name prefix.",
    )
    group.addoption(
        "--testcontainers-clean",
        action="store_true",
        default=False,
        dest="testcontainers_clean",
        help="Stop+remove all <project>-tc-* containers, then exit.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("testcontainers_disabled"):
        reuse.set_cli_disabled(True)
    project = config.getoption("testcontainers_project")
    if project:
        reuse.set_cli_project(project)
    if config.getoption("testcontainers_no_reuse"):
        reuse.set_cli_reuse(False)
    elif config.getoption("testcontainers_reuse"):
        reuse.set_cli_reuse(True)


def pytest_cmdline_main(config: pytest.Config) -> int | None:
    """Implement ``--testcontainers-clean``: prune named containers, exit 0."""
    if not config.getoption("testcontainers_clean"):
        return None
    project = reuse.project_name()
    prefix = f"{reuse.sanitize_component(project)}-tc-"
    try:
        import docker  # local import — never trigger at module load
    except ImportError:
        sys.stderr.write("[pytest-testcontainers] docker-py not installed\n")
        return 1
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        sys.stderr.write(f"[pytest-testcontainers] cannot reach Docker daemon: {exc!r}\n")
        return 1
    removed = 0
    for container in client.containers.list(all=True):
        name = container.name or ""
        if name.startswith(prefix):
            try:
                container.remove(force=True)
                removed += 1
                sys.stdout.write(f"[pytest-testcontainers] removed: {name}\n")
            except Exception as exc:
                sys.stderr.write(f"[pytest-testcontainers] failed to remove {name}: {exc!r}\n")
    sys.stdout.write(
        f"[pytest-testcontainers] {removed} container(s) removed (prefix={prefix!r})\n"
    )
    return 0
