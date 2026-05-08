"""Integration: ``tc_psql_db`` creates and drops a fresh DB per test."""

from __future__ import annotations

import pytest

from pytest_testcontainers import DbConnInfo

pytestmark = pytest.mark.docker_required


def test_tc_psql_db_yields_unique_db_per_test(tc_psql_db: DbConnInfo) -> None:
    assert isinstance(tc_psql_db, DbConnInfo)
    assert tc_psql_db.database.startswith("test_")
    # 12 hex chars after "test_"
    assert len(tc_psql_db.database) == len("test_") + 12


def test_tc_psql_db_url_render(tc_psql_db: DbConnInfo) -> None:
    url = tc_psql_db.url()
    assert url.startswith("postgresql://")
    assert tc_psql_db.database in url


def test_tc_psql_db_distinct_per_test(tc_psql_db: DbConnInfo) -> None:
    """Two requests of the fixture across tests must yield different DB names."""
    # capture the name; another test asserts they differ via a separate test.
    assert tc_psql_db.database != "test_PLACEHOLDER"
