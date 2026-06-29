"""Named pytest fixtures + maker convention on top of testcontainers-python."""

from __future__ import annotations

from pytest_testcontainers._internal.conn_info import DbConnInfo
from pytest_testcontainers.errors import (
    CleanSessionFixtureError,
    ContainerStartError,
    DockerNotRunningError,
    NameTemplateError,
    PytestTestcontainersError,
    ReuseConflictError,
)
from pytest_testcontainers.makers import (
    make_container,
    make_mongo,
    make_mysql,
    make_postgres,
    make_rabbitmq,
    make_redis,
)

__all__ = [
    "CleanSessionFixtureError",
    "ContainerStartError",
    "DbConnInfo",
    "DockerNotRunningError",
    "NameTemplateError",
    "PytestTestcontainersError",
    "ReuseConflictError",
    "make_container",
    "make_mongo",
    "make_mysql",
    "make_postgres",
    "make_rabbitmq",
    "make_redis",
]

__version__ = "0.2.2"
