"""Integration: ``make_mongo()`` boots a real MongoDB container."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_mongo

pytestmark = pytest.mark.docker_required


def test_make_mongo_default_starts_and_exposes_port() -> None:
    with make_mongo() as m:
        assert int(m.get_exposed_port(27017)) > 0
