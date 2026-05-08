"""Integration: ``make_postgres()`` boots a real PG container."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_postgres

pytestmark = pytest.mark.docker_required


def test_make_postgres_default_starts_and_exposes_port() -> None:
    with make_postgres() as pg:
        host = pg.get_container_host_ip()
        port = int(pg.get_exposed_port(5432))
        assert host
        assert port > 0


def test_make_postgres_custom_image_and_creds() -> None:
    with make_postgres(
        image="postgres:16",
        username="alice",
        password="s3cret",
        database="appdb",
    ) as pg:
        assert pg.username == "alice"
        assert pg.password == "s3cret"
        assert pg.dbname == "appdb"


def test_make_postgres_kwargs_passthrough() -> None:
    with make_postgres(env={"POSTGRES_INITDB_ARGS": "--encoding=UTF8"}) as pg:
        assert int(pg.get_exposed_port(5432)) > 0
