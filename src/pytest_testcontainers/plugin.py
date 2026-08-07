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

from pytest_testcontainers import containers, reuse
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
        "--testcontainers-name-template",
        action="store",
        default=None,
        dest="testcontainers_name_template",
        help=(
            "Template for container names. Placeholders: {project} {service} "
            "{branch} {dir} {dirtag} {worker} {rand}."
        ),
    )
    group.addoption(
        "--testcontainers-clean",
        action="store_true",
        default=False,
        dest="testcontainers_clean",
        help=(
            "Stop+remove every container this library could have left behind "
            "(anything labelled org.testcontainers=true, any testcontainers-ryuk-*, "
            "plus <project>-tc-*), then exit. On a shared host this also removes "
            "containers belonging to a concurrently running session — use "
            "--testcontainers-clean-project to stay within this project."
        ),
    )
    group.addoption(
        "--testcontainers-clean-project",
        action="store_true",
        default=False,
        dest="testcontainers_clean_project",
        help="Like --testcontainers-clean but limited to <project>-tc-* names.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("testcontainers_disabled"):
        reuse.set_cli_disabled(True)
    project = config.getoption("testcontainers_project")
    if project:
        reuse.set_cli_project(project)
    name_template = config.getoption("testcontainers_name_template")
    if name_template:
        reuse.set_cli_name_template(name_template)
    if config.getoption("testcontainers_no_reuse"):
        reuse.set_cli_reuse(False)
    elif config.getoption("testcontainers_reuse"):
        reuse.set_cli_reuse(True)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Close out the Ryuk reaper once session fixtures have been torn down.

    Session-scoped fixtures finalize before ``pytest_unconfigure``, so by
    now our own containers are gone and Ryuk has nothing left to watch.
    Without this it lingers for the remainder of its reconnection timeout
    — one privileged container per worker, for ~60 s after every run.

    Guarded so a session that never started a container does not pay for
    importing testcontainers just to discover there is nothing to do.
    """
    if containers.ryuk_maybe_running():
        containers.shutdown_ryuk()


# Ryuk's own container is not tagged with the session label it reaps by, so
# it has to be matched by name.
_RYUK_NAME_PREFIX = "testcontainers-ryuk-"
_TESTCONTAINERS_LABEL = "org.testcontainers"


def _docker_client():  # type: ignore[no-untyped-def]
    """Build a docker-py client honoring the active ``docker context``."""
    import docker  # local import — never trigger at module load

    # OrbStack / colima / Docker Desktop put the socket somewhere from_env()
    # would not find on its own; same treatment as the launch path.
    from pytest_testcontainers._internal.docker_health import ensure_docker_host_env

    ensure_docker_host_env()
    client = docker.from_env()
    client.ping()
    return client


def _select_containers(client, prefix: str, *, project_only: bool) -> list:  # type: ignore[no-untyped-def]
    """Containers ``--testcontainers-clean`` should remove.

    The ``org.testcontainers=true`` label is the primary selector — it is
    the only thing that reaches containers started without an explicit
    name (which is how ``pytest-testcontainers-django`` used to run), and
    testcontainers stamps it on every container it creates, reuse or not.
    The name prefix stays as a second selector for anything unlabelled:
    containers left by an older release, or started by hand to match the
    naming scheme.
    """
    selected = []
    for container in client.containers.list(all=True):
        name = container.name or ""
        if name.startswith(prefix):
            selected.append(container)
            continue
        if project_only:
            continue
        labels = getattr(container, "labels", None) or {}
        if labels.get(_TESTCONTAINERS_LABEL) == "true" or name.startswith(_RYUK_NAME_PREFIX):
            selected.append(container)
    return selected


def pytest_cmdline_main(config: pytest.Config) -> int | None:
    """Implement ``--testcontainers-clean``: prune containers, exit 0."""
    project_only = bool(config.getoption("testcontainers_clean_project"))
    if not (config.getoption("testcontainers_clean") or project_only):
        return None
    project = reuse.project_name()
    prefix = f"{reuse.sanitize_component(project)}-tc-"
    try:
        client = _docker_client()
    except ImportError:
        sys.stderr.write("[pytest-testcontainers] docker-py not installed\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"[pytest-testcontainers] cannot reach Docker daemon: {exc!r}\n")
        return 1

    removed = 0
    for container in _select_containers(client, prefix, project_only=project_only):
        name = container.name or ""
        try:
            container.remove(force=True)
        except Exception as exc:
            sys.stderr.write(f"[pytest-testcontainers] failed to remove {name}: {exc!r}\n")
            continue
        removed += 1
        sys.stdout.write(f"[pytest-testcontainers] removed: {name}\n")

    scope = f"prefix={prefix!r}" if project_only else f"prefix={prefix!r} + testcontainers label"
    sys.stdout.write(f"[pytest-testcontainers] {removed} container(s) removed ({scope})\n")
    return 0
