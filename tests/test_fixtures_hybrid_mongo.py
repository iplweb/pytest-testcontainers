"""Integration: ``tc_mongo_db`` fixture yields a fresh DB name per test."""

from __future__ import annotations

import pytest

from pytest_testcontainers import DbConnInfo

pytestmark = pytest.mark.docker_required


def test_tc_mongo_db_yields_fresh_name(tc_mongo_db: DbConnInfo) -> None:
    assert isinstance(tc_mongo_db, DbConnInfo)
    assert tc_mongo_db.database.startswith("test_")
    assert len(tc_mongo_db.database) == len("test_") + 12
