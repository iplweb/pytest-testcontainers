"""Honor the active ``docker context`` when resolving the daemon socket.

``docker.from_env()`` honors ``DOCKER_HOST`` but — unlike the ``docker`` CLI —
ignores the active ``docker context``. On OrbStack / colima / a stopped Docker
Desktop the daemon lives on a non-default socket and ``/var/run/docker.sock``
is absent or a dangling symlink, so ``from_env()`` reports the daemon as
unreachable (and leaks the half-open socket) even though ``docker ps`` works.

The plugin bridges the gap by exporting ``DOCKER_HOST`` from the active context
before any docker-py / testcontainers / Ryuk client is built. An explicit
``DOCKER_HOST`` always wins; no resolvable context falls back to the platform
default socket. These tests pin that precedence.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from pytest_testcontainers._internal import docker_health


def test_ensure_docker_host_env_exports_active_context_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCKER_HOST", raising=False)

    class FakeCtx:
        Host = "unix:///Users/me/.orbstack/run/docker.sock"

    monkeypatch.setattr(docker_health, "_active_docker_context", lambda: FakeCtx())

    docker_health.ensure_docker_host_env()

    assert os.environ["DOCKER_HOST"] == "unix:///Users/me/.orbstack/run/docker.sock"


def test_ensure_docker_host_env_respects_existing_docker_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/explicit.sock")

    def fail() -> None:
        raise AssertionError("must not resolve context when DOCKER_HOST is set")

    monkeypatch.setattr(docker_health, "_active_docker_context", fail)

    docker_health.ensure_docker_host_env()

    assert os.environ["DOCKER_HOST"] == "unix:///tmp/explicit.sock"


def test_ensure_docker_host_env_noop_without_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(docker_health, "_active_docker_context", lambda: None)

    docker_health.ensure_docker_host_env()

    assert "DOCKER_HOST" not in os.environ


def test_active_docker_context_returns_current_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docker.context import ContextAPI

    sentinel = object()
    monkeypatch.setattr(ContextAPI, "get_current_context", classmethod(lambda cls: sentinel))

    assert docker_health._active_docker_context() is sentinel


def test_active_docker_context_swallows_errors_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docker.context import ContextAPI

    def boom(cls) -> None:
        raise RuntimeError("context resolution exploded")

    monkeypatch.setattr(ContextAPI, "get_current_context", classmethod(boom))

    assert docker_health._active_docker_context() is None


def test_check_docker_daemon_exports_docker_host_from_context_before_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_docker_daemon must export DOCKER_HOST from the active context so
    the daemon ping — and every later testcontainers/Ryuk client — resolves
    the same socket the CLI uses."""
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    docker_health.reset_cache()

    class FakeCtx:
        Host = "unix:///Users/me/.orbstack/run/docker.sock"

    monkeypatch.setattr(docker_health, "_active_docker_context", lambda: FakeCtx())

    class _OK:
        def ping(self) -> None:
            pass

    fake = type("M", (), {})()
    fake.from_env = lambda: _OK()
    with patch.dict("sys.modules", {"docker": fake}):
        docker_health.check_docker_daemon()

    assert os.environ["DOCKER_HOST"] == "unix:///Users/me/.orbstack/run/docker.sock"
