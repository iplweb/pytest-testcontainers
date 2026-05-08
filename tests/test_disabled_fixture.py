"""Integration: ``--no-testcontainers`` makes session fixtures raise UsageError."""

from __future__ import annotations

import pytest


def test_no_testcontainers_makes_tc_psql_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS", "0")
    from pytest_testcontainers.fixtures import _check_enabled

    with pytest.raises(pytest.UsageError):
        _check_enabled()
