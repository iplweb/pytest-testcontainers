"""Shared test fixtures + isolation helpers.

Most unit tests poke module-level state (CLI overrides, ping cache,
fallback warning gates). The ``_clean_state`` autouse fixture resets
all of that between tests so we never leak across the suite.
"""

from __future__ import annotations

import os

import pytest

from pytest_testcontainers import containers, reuse
from pytest_testcontainers._internal import clean_session_admin, docker_health


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reuse.reset_cli_state()
    docker_health.reset_cache()
    clean_session_admin._reset_warnings_for_tests()
    containers._reset_teardown_state_for_tests()
    # `ensure_docker_host_env()` mutates os.environ["DOCKER_HOST"] directly, so
    # monkeypatch can't undo it. Snapshot the pre-test value and restore it in
    # teardown so a context-export in one test never leaks into the next.
    orig_docker_host = os.environ.get("DOCKER_HOST")
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
    yield
    reuse.reset_cli_state()
    docker_health.reset_cache()
    clean_session_admin._reset_warnings_for_tests()
    containers._reset_teardown_state_for_tests()
    if orig_docker_host is None:
        os.environ.pop("DOCKER_HOST", None)
    else:
        os.environ["DOCKER_HOST"] = orig_docker_host


def _docker_available() -> bool:
    try:
        import docker
    except ImportError:
        return False
    # Mirror the plugin: honor the active `docker context` so docker_required
    # tests actually run on OrbStack / colima / Docker Desktop (where the
    # default socket is absent or a dangling symlink) instead of being skipped.
    docker_health.ensure_docker_host_env()
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``docker_required`` tests if no daemon is reachable."""
    if _docker_available():
        return
    skip_marker = pytest.mark.skip(
        reason="Docker daemon not reachable; skipping docker_required tests."
    )
    for item in items:
        if "docker_required" in item.keywords:
            item.add_marker(skip_marker)


# Allow tests to override the working directory used for pyproject lookup.
@pytest.fixture
def tmp_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# Helper to disable PYTEST_TESTCONTAINERS env from environment for a test
@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Convenience alias for monkeypatch with `setenv`/`delenv`."""
    return monkeypatch


# Re-expose docker availability as a fixture for explicit gating.
@pytest.fixture(scope="session")
def docker_available() -> bool:
    return _docker_available()
