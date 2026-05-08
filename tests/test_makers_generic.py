"""Integration: ``make_container(Cls, ...)`` works with arbitrary container classes."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_container

pytestmark = pytest.mark.docker_required


def test_make_container_with_redis_class() -> None:
    """Use the upstream RedisContainer through the generic escape hatch."""
    from testcontainers.redis import RedisContainer

    with make_container(RedisContainer, image="redis:7-alpine") as r:
        assert int(r.get_exposed_port(6379)) > 0
