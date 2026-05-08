"""Daemon-ping behavior with docker-py mocked out."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pytest_testcontainers._internal import docker_health
from pytest_testcontainers.errors import DockerNotRunningError


class _FakeClient:
    def __init__(self, ping_raises: BaseException | None = None) -> None:
        self._raises = ping_raises

    def ping(self) -> None:
        if self._raises is not None:
            raise self._raises


def test_ping_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_docker_module = type("M", (), {})()
    fake_docker_module.from_env = lambda: _FakeClient(ConnectionError("nope"))

    with (
        patch.dict("sys.modules", {"docker": fake_docker_module}),
        pytest.raises(DockerNotRunningError) as excinfo,
    ):
        docker_health.check_docker_daemon()
    assert "ping() failed" in str(excinfo.value)


def test_ping_failure_does_not_poison_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed ping must not flip the success cache; next call must retry."""
    state = {"calls": 0}

    class _Once:
        def ping(self) -> None:
            state["calls"] += 1
            if state["calls"] == 1:
                raise OSError("transient")

    fake = type("M", (), {})()
    fake.from_env = lambda: _Once()
    with patch.dict("sys.modules", {"docker": fake}):
        with pytest.raises(DockerNotRunningError):
            docker_health.check_docker_daemon()
        # Second call retries — the cache only stores success.
        docker_health.check_docker_daemon()
    assert state["calls"] == 2


def test_ping_success_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    class _OK:
        def ping(self) -> None:
            state["calls"] += 1

    fake = type("M", (), {})()
    fake.from_env = lambda: _OK()
    with patch.dict("sys.modules", {"docker": fake}):
        docker_health.check_docker_daemon()
        docker_health.check_docker_daemon()
        docker_health.check_docker_daemon()
    assert state["calls"] == 1


def test_ping_skipped_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK", "1")
    # Even with a broken docker module loaded, no probe must run.
    fake = type("M", (), {})()

    def _boom() -> None:
        raise AssertionError("ping must NOT run when env-skip is set")

    fake.from_env = _boom
    with patch.dict("sys.modules", {"docker": fake}):
        docker_health.check_docker_daemon()  # must not raise
