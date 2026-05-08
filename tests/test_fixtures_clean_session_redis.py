"""Integration: ``tc_redis_clean`` flushes Redis between tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.docker_required


def _set_and_get(container, key: str, value: str) -> str:
    raw = container._container
    raw.exec_run(["redis-cli", "SET", key, value])
    code, output = raw.exec_run(["redis-cli", "GET", key])
    assert code == 0
    return (output or b"").decode().strip()


def test_redis_clean_first_run_seeds_then_flushes(tc_redis_clean) -> None:
    """Sets a key; teardown FLUSHALLs it before the next test runs."""
    assert _set_and_get(tc_redis_clean, "alpha", "1") == "1"


def test_redis_clean_second_run_sees_no_state(tc_redis_clean) -> None:
    """The previous test's ``alpha`` must be gone."""
    raw = tc_redis_clean._container
    code, output = raw.exec_run(["redis-cli", "GET", "alpha"])
    assert code == 0
    # redis-cli prints empty string for a missing key; strip and assert empty.
    assert (output or b"").decode().strip() == ""
