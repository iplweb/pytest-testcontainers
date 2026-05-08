"""Integration: ``make_mysql()`` boots a real MySQL container."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_mysql

pytestmark = pytest.mark.docker_required


def test_make_mysql_default_starts_and_exposes_port() -> None:
    with make_mysql() as my:
        assert int(my.get_exposed_port(3306)) > 0
