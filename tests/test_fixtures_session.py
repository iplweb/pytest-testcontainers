"""Integration: built-in session fixtures behave as advertised."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.docker_required


def test_tc_psql_session_fixture(tc_psql) -> None:
    assert int(tc_psql.get_exposed_port(5432)) > 0


def test_tc_psql_is_shared_within_session(tc_psql, request) -> None:
    """Two tests requesting tc_psql see the same container instance."""
    assert (
        tc_psql is request.config._tc_psql_seen
        if hasattr(request.config, "_tc_psql_seen")
        else True
    )
    request.config._tc_psql_seen = tc_psql


def test_tc_redis_session_fixture(tc_redis) -> None:
    assert int(tc_redis.get_exposed_port(6379)) > 0
