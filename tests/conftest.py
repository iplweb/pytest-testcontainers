"""Shared test fixtures + isolation helpers.

Most unit tests poke module-level state (CLI overrides, ping cache,
fallback warning gates). The ``_clean_state`` autouse fixture resets
all of that between tests so we never leak across the suite.
"""

from __future__ import annotations

import pytest

from pytest_testcontainers import reuse
from pytest_testcontainers._internal import clean_session_admin, docker_health


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reuse.reset_cli_state()
    docker_health.reset_cache()
    clean_session_admin._reset_warnings_for_tests()
    for var in (
        "PYTEST_TESTCONTAINERS",
        "PYTEST_TESTCONTAINERS_REUSE",
        "PYTEST_TESTCONTAINERS_PROJECT",
        "PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK",
        "PYTEST_TESTCONTAINERS_QUIET",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    reuse.reset_cli_state()
    docker_health.reset_cache()
    clean_session_admin._reset_warnings_for_tests()


def _docker_available() -> bool:
    try:
        import docker
    except ImportError:
        return False
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
