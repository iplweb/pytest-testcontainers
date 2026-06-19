"""Integration: reuse-mode smoke test.

Two consecutive maker invocations must reuse the same container when
``PYTEST_TESTCONTAINERS_REUSE=1``. We tear down the named container
after the test to keep CI tidy.
"""

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
        container = client.containers.get(name)
    except NotFound:
        return
    container.remove(force=True)


def test_reuse_keeps_same_container_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "ptc-smoke")
    name = container_name_for("redis")
    _remove_named(name)
    try:
        with make_redis() as r1:
            id1 = r1._container.id
        with make_redis() as r2:
            id2 = r2._container.id
        assert id1 == id2, "reuse mode must reuse the named container"
    finally:
        _remove_named(name)
