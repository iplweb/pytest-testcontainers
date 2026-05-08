"""Integration: ``make_redis()`` boots a real Redis container."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_redis

pytestmark = pytest.mark.docker_required


def test_make_redis_default_starts_and_exposes_port() -> None:
    with make_redis() as r:
        assert int(r.get_exposed_port(6379)) > 0
