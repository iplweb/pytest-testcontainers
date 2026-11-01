"""Pytest fixtures registered by the plugin.

Three families per service:

1. **Default session** (``tc_<svc>``) — one container per worker session.
2. **Function-scoped** (``tc_<svc>_func``) — one container per test.
3. **Clean-session** (``tc_<svc>_db`` / ``tc_redis_clean``) — session
   container, per-test fresh DB/keyspace.

Each fixture is replaced with a stub that raises :class:`pytest.UsageError`
when ``--no-testcontainers`` (or ``PYTEST_TESTCONTAINERS=0``) is active.
The maker layer is unaffected — it stays usable from non-pytest code.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import pytest

from pytest_testcontainers._internal.clean_session_admin import (
    create_mysql_db,
    create_postgres_db,
    drop_mongo_db,
    drop_mysql_db,
    drop_postgres_db,
    flush_redis,
)
from pytest_testcontainers._internal.conn_info import DbConnInfo
from pytest_testcontainers._internal.port_resolver import normalize_host
from pytest_testcontainers.errors import (
    CleanSessionFixtureError,
    DockerNotRunningError,
    PytestTestcontainersError,
)
from pytest_testcontainers.makers import (
    make_mongo,
    make_mysql,
    make_postgres,
    make_rabbitmq,
    make_redis,
)
from pytest_testcontainers.reuse import is_plugin_disabled

if TYPE_CHECKING:
    from collections.abc import Iterator

    from testcontainers.mongodb import MongoDbContainer
    from testcontainers.mysql import MySqlContainer
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.rabbitmq import RabbitMqContainer
    from testcontainers.community.redis import RedisContainer

__all__ = [
    "tc_mongo",
    "tc_mongo_db",
    "tc_mongo_func",
    "tc_mysql",
    "tc_mysql_db",
    "tc_mysql_func",
    "tc_psql",
    "tc_psql_db",
    "tc_psql_func",
    "tc_rabbitmq",
    "tc_rabbitmq_func",
    "tc_redis",
    "tc_redis_clean",
    "tc_redis_func",
]

_DISABLED_MSG = (
    "[pytest-testcontainers] disabled via --no-testcontainers / "
    "PYTEST_TESTCONTAINERS=0; user expected to provide service externally"
)


def _check_enabled() -> None:
    if is_plugin_disabled():
        raise pytest.UsageError(_DISABLED_MSG)


def _to_usage_error(exc: PytestTestcontainersError) -> pytest.UsageError:
    """Convert plugin-internal exceptions to user-friendly pytest.UsageError."""
    if isinstance(exc, DockerNotRunningError):
        msg = (
            "[pytest-testcontainers] Docker daemon is not reachable.\n"
            "Is Docker Desktop / colima / Rancher Desktop running?\n"
            "\n"
            "Options:\n"
            "  - start it and re-run pytest, OR\n"
            "  - disable the plugin: --no-testcontainers (or "
            "PYTEST_TESTCONTAINERS=0), OR\n"
            "  - point at remote Docker via DOCKER_HOST.\n"
            "\n"
            f"Underlying error: {exc}"
        )
        return pytest.UsageError(msg)
    return pytest.UsageError(f"[pytest-testcontainers] {exc}")


# --------------------------------------------------------------------------
# Postgres
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tc_psql() -> Iterator[PostgresContainer]:
    """Session-scoped Postgres container (default ``postgres:16``)."""
    _check_enabled()
    try:
        with make_postgres() as pg:
            yield pg
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_psql_func() -> Iterator[PostgresContainer]:
    """Function-scoped fresh Postgres container — almost always wrong, see README."""
    _check_enabled()
    try:
        with make_postgres() as pg:
            yield pg
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_psql_db(tc_psql: PostgresContainer) -> Iterator[DbConnInfo]:
    """Per-test fresh Postgres database on the session container."""
    name = f"test_{secrets.token_hex(6)}"
    create_postgres_db(tc_psql, name)
    try:
        yield DbConnInfo(
            host=normalize_host(tc_psql.get_container_host_ip()),
            port=int(tc_psql.get_exposed_port(5432)),
            username=tc_psql.username,
            password=tc_psql.password,
            database=name,
        )
    finally:
        try:
            drop_postgres_db(tc_psql, name)
        except CleanSessionFixtureError:
            import logging

            logging.getLogger("pytest_testcontainers").exception(
                "tc_psql_db: DROP DATABASE %r failed during teardown", name
            )
            raise


# --------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tc_redis() -> Iterator[RedisContainer]:
    _check_enabled()
    try:
        with make_redis() as r:
            yield r
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_redis_func() -> Iterator[RedisContainer]:
    _check_enabled()
    try:
        with make_redis() as r:
            yield r
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_redis_clean(tc_redis: RedisContainer) -> Iterator[RedisContainer]:
    """Session Redis container with ``FLUSHALL`` between tests."""
    yield tc_redis
    flush_redis(tc_redis)


# --------------------------------------------------------------------------
# MySQL
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tc_mysql() -> Iterator[MySqlContainer]:
    _check_enabled()
    try:
        with make_mysql() as my:
            yield my
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_mysql_func() -> Iterator[MySqlContainer]:
    _check_enabled()
    try:
        with make_mysql() as my:
            yield my
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_mysql_db(tc_mysql: MySqlContainer) -> Iterator[DbConnInfo]:
    name = f"test_{secrets.token_hex(6)}"
    create_mysql_db(tc_mysql, name)
    try:
        yield DbConnInfo(
            host=normalize_host(tc_mysql.get_container_host_ip()),
            port=int(tc_mysql.get_exposed_port(3306)),
            username=tc_mysql.username,
            password=tc_mysql.password,
            database=name,
        )
    finally:
        try:
            drop_mysql_db(tc_mysql, name)
        except CleanSessionFixtureError:
            import logging

            logging.getLogger("pytest_testcontainers").exception(
                "tc_mysql_db: DROP DATABASE %r failed during teardown", name
            )
            raise


# --------------------------------------------------------------------------
# MongoDB
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tc_mongo() -> Iterator[MongoDbContainer]:
    _check_enabled()
    try:
        with make_mongo() as m:
            yield m
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_mongo_func() -> Iterator[MongoDbContainer]:
    _check_enabled()
    try:
        with make_mongo() as m:
            yield m
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_mongo_db(tc_mongo: MongoDbContainer) -> Iterator[DbConnInfo]:
    name = f"test_{secrets.token_hex(6)}"
    try:
        yield DbConnInfo(
            host=normalize_host(tc_mongo.get_container_host_ip()),
            port=int(tc_mongo.get_exposed_port(27017)),
            username=tc_mongo.username,
            password=tc_mongo.password,
            database=name,
        )
    finally:
        try:
            drop_mongo_db(tc_mongo, name)
        except CleanSessionFixtureError:
            import logging

            logging.getLogger("pytest_testcontainers").exception(
                "tc_mongo_db: dropDatabase %r failed during teardown", name
            )
            raise


# --------------------------------------------------------------------------
# RabbitMQ — no clean-session fixture (cleanup not obvious; user wires their own)
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tc_rabbitmq() -> Iterator[RabbitMqContainer]:
    _check_enabled()
    try:
        with make_rabbitmq() as rmq:
            yield rmq
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc


@pytest.fixture
def tc_rabbitmq_func() -> Iterator[RabbitMqContainer]:
    _check_enabled()
    try:
        with make_rabbitmq() as rmq:
            yield rmq
    except PytestTestcontainersError as exc:
        raise _to_usage_error(exc) from exc
