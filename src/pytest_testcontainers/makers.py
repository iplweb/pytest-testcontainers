"""Public maker functions: ``make_postgres``, ``make_redis``, … .

Each maker is a context manager wrapping the corresponding
``testcontainers-python`` class. The maker layer holds:

* daemon ping (cached on success);
* reuse-name resolution + Ryuk disable + lookup-by-name choreography;
* atexit safety net for one-shot (function-scoped) mode.

The fixture layer (``fixtures.py``) is deliberately thin — it just calls
these makers from inside ``@pytest.fixture`` functions.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from pytest_testcontainers._internal.docker_health import check_docker_daemon
from pytest_testcontainers.containers import (
    bind_to_running,
    deregister_atexit_stop,
    disable_ryuk_once,
    find_existing_container,
    register_atexit_stop,
    restart_stopped_or_raise,
    start_or_raise,
)
from pytest_testcontainers.reuse import (
    container_name_for,
    is_reuse_enabled,
    sanitize_component,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from testcontainers.core.container import DockerContainer


# --------------------------------------------------------------------------
# Generic core
# --------------------------------------------------------------------------
def _service_slug_for(container_cls: type) -> str:
    """Derive a service slug from ``Cls.__name__`` (§16 #5)."""
    name = container_cls.__name__.lower()
    if name.endswith("container"):
        name = name[: -len("container")]
    return sanitize_component(name) or "container"


def _apply_env(container: DockerContainer, env: dict[str, str] | None) -> None:
    if not env:
        return
    for key, value in env.items():
        container.with_env(key, value)


@contextmanager
def _make_generic(
    container_cls: type[DockerContainer],
    *constructor_args: Any,
    service_slug: str,
    reuse_name: str | None,
    env: dict[str, str] | None,
    constructor_kwargs: dict[str, Any] | None = None,
) -> Iterator[DockerContainer]:
    """Shared lifecycle for every maker.

    ``service_slug`` is the short slug used in reuse-name composition
    (``psql``, ``redis``, …, or derived from ``Cls.__name__`` for the
    generic ``make_container``).
    """
    constructor_kwargs = dict(constructor_kwargs or {})

    check_docker_daemon()

    reuse_on = is_reuse_enabled() or reuse_name is not None
    name = container_name_for(service_slug, override=reuse_name, reuse_mode=reuse_on)

    if reuse_on:
        disable_ryuk_once()

    instance: DockerContainer | None = None
    is_bound_to_existing = False

    if reuse_on:
        existing = find_existing_container(name)
        if existing is not None:
            status = getattr(existing, "status", "")
            if status == "running":
                instance = bind_to_running(
                    container_cls, existing, *constructor_args, **constructor_kwargs
                )
                _apply_env(instance, env)
                is_bound_to_existing = True
            elif status in {"exited", "created", "paused"}:
                restart_stopped_or_raise(existing, name)
                instance = bind_to_running(
                    container_cls, existing, *constructor_args, **constructor_kwargs
                )
                _apply_env(instance, env)
                is_bound_to_existing = True
            # Any other status (e.g. "removing", "dead") — treat as
            # "not usable", fall through to fresh start.

    if instance is None:
        instance = container_cls(*constructor_args, **constructor_kwargs)
        instance.with_name(name)
        _apply_env(instance, env)

        image_for_msg = constructor_kwargs.get("image") or (
            constructor_args[0] if constructor_args else container_cls.__name__
        )

        # Arm teardown BEFORE starting. `.start()` blocks until the wait
        # strategy passes — seconds for a Postgres seeded from an init
        # script — and the container exists for that whole time. Arming
        # afterwards leaves the most abort-prone window unprotected, and a
        # start that fails its wait strategy leaves a container behind too.
        # `stop()` is a no-op while the wrapper has no container yet.
        if not reuse_on:
            register_atexit_stop(instance)

        start_or_raise(instance, str(image_for_msg))

    try:
        yield instance
    finally:
        # Reuse mode: leave the container alive for the next run.
        # Bound-to-existing: we never owned it, never stop it.
        # Any exception raised in the with-body propagates either way —
        # we only do cleanup work, never `return` from finally.
        if not (reuse_on or is_bound_to_existing):
            # Deregister the atexit callback before stopping so the
            # safety net doesn't double-fire on a now-removed container.
            deregister_atexit_stop(instance)
            try:
                instance.stop()
            except Exception:
                # Logged + swallowed: cleanup-time failures must not
                # mask the original test failure. Per §4.1.2.
                import logging

                logging.getLogger("pytest_testcontainers").exception(
                    "cleanup: container.stop() failed for %s", service_slug
                )


# --------------------------------------------------------------------------
# Service-specific makers
# --------------------------------------------------------------------------
def make_postgres(
    image: str = "postgres:16",
    *,
    username: str = "test",
    password: str = "test",
    database: str = "test",
    port: int = 5432,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs: Any,
):
    """Context manager wrapping ``PostgresContainer``."""
    from testcontainers.community.postgres import PostgresContainer

    constructor_kwargs: dict[str, Any] = {
        "image": image,
        "username": username,
        "password": password,
        "dbname": database,
        "port": port,
        **kwargs,
    }
    return _make_generic(
        PostgresContainer,
        service_slug="psql",
        reuse_name=reuse_name,
        env=env,
        constructor_kwargs=constructor_kwargs,
    )


def make_redis(
    image: str = "redis:7-alpine",
    *,
    port: int = 6379,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs: Any,
):
    """Context manager wrapping ``RedisContainer``."""
    from testcontainers.community.redis import RedisContainer

    constructor_kwargs: dict[str, Any] = {
        "image": image,
        "port": port,
        **kwargs,
    }
    return _make_generic(
        RedisContainer,
        service_slug="redis",
        reuse_name=reuse_name,
        env=env,
        constructor_kwargs=constructor_kwargs,
    )


def make_mysql(
    image: str = "mysql:8",
    *,
    username: str = "test",
    password: str = "test",
    database: str = "test",
    root_password: str = "test",
    port: int = 3306,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs: Any,
):
    """Context manager wrapping ``MySqlContainer``."""
    from testcontainers.mysql import MySqlContainer

    constructor_kwargs: dict[str, Any] = {
        "image": image,
        "username": username,
        "password": password,
        "dbname": database,
        "root_password": root_password,
        "port": port,
        **kwargs,
    }
    return _make_generic(
        MySqlContainer,
        service_slug="mysql",
        reuse_name=reuse_name,
        env=env,
        constructor_kwargs=constructor_kwargs,
    )


def make_mongo(
    image: str = "mongo:7",
    *,
    username: str = "test",
    password: str = "test",
    port: int = 27017,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs: Any,
):
    """Context manager wrapping ``MongoDbContainer``."""
    from testcontainers.mongodb import MongoDbContainer

    constructor_kwargs: dict[str, Any] = {
        "image": image,
        "username": username,
        "password": password,
        "port": port,
        **kwargs,
    }
    return _make_generic(
        MongoDbContainer,
        service_slug="mongo",
        reuse_name=reuse_name,
        env=env,
        constructor_kwargs=constructor_kwargs,
    )


def make_rabbitmq(
    image: str = "rabbitmq:3-management",
    *,
    username: str = "guest",
    password: str = "guest",
    port: int = 5672,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs: Any,
):
    """Context manager wrapping ``RabbitMqContainer``."""
    from testcontainers.rabbitmq import RabbitMqContainer

    constructor_kwargs: dict[str, Any] = {
        "image": image,
        "username": username,
        "password": password,
        "port": port,
        **kwargs,
    }
    return _make_generic(
        RabbitMqContainer,
        service_slug="rabbitmq",
        reuse_name=reuse_name,
        env=env,
        constructor_kwargs=constructor_kwargs,
    )


def make_container(
    container_cls: type[DockerContainer],
    *args: Any,
    reuse_name: str | None = None,
    env: dict[str, str] | None = None,
    **kwargs: Any,
):
    """Generic escape hatch for services we don't ship a maker for.

    Same plumbing (daemon ping, reuse, atexit, Ryuk-disable-when-reuse).
    """
    return _make_generic(
        container_cls,
        *args,
        service_slug=_service_slug_for(container_cls),
        reuse_name=reuse_name,
        env=env,
        constructor_kwargs=kwargs,
    )
