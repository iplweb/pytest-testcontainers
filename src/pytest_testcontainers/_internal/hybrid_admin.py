"""Admin commands behind hybrid fixtures.

Each service gets a tiny dispatch table:

* ``create_<service>_db`` / ``drop_<service>_db`` (Postgres, MySQL, Mongo)
* ``flush_redis`` (Redis only — exec-only by design)

Both branches (Python client + ``docker exec``) raise the same
:class:`HybridFixtureError` so user code sees one exception type.

All commands invoked inside the container go through docker-py's
``exec_run`` with an argv list (never a shell string). Generated
identifiers are produced by us via ``secrets.token_hex`` upstream and
are additionally quoted defensively at the SQL layer.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
import threading
from typing import TYPE_CHECKING, Any

from pytest_testcontainers.errors import HybridFixtureError

if TYPE_CHECKING:
    from testcontainers.core.container import DockerContainer

logger = logging.getLogger("pytest_testcontainers")

_warned_lock = threading.Lock()
_warned_services: set[str] = set()


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_quiet() -> bool:
    return _is_truthy(os.environ.get("PYTEST_TESTCONTAINERS_QUIET"))


def _maybe_warn_fallback(service: str, install_hint: str) -> None:
    global _warned_services
    if _is_quiet():
        return
    with _warned_lock:
        if service in _warned_services:
            return
        _warned_services.add(service)
    sys.stderr.write(
        f"[pytest-testcontainers] {service} hybrid using docker exec fallback\n"
        f"(~100ms per admin call). For ~20x speedup install the client:\n"
        f"    pip install {install_hint}\n"
        f"or via the convenience extra:\n"
        f"    pip install pytest-testcontainers[hybrid]\n"
        f"Suppress this notice with PYTEST_TESTCONTAINERS_QUIET=1.\n"
    )


def _reset_warnings_for_tests() -> None:
    global _warned_services
    with _warned_lock:
        _warned_services = set()


def _run_in_container(
    container: DockerContainer,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute ``argv`` inside the container; return ``(exit_code, stdout, stderr)``."""
    raw_container = getattr(container, "_container", None)
    if raw_container is None:
        raise HybridFixtureError(
            "container has no underlying docker-py reference",
            command=" ".join(shlex.quote(a) for a in argv),
            stderr="container._container is None",
            exit_code=None,
        )
    exec_kwargs: dict[str, Any] = {"demux": True}
    if env:
        exec_kwargs["environment"] = env
    exit_code, output = raw_container.exec_run(argv, **exec_kwargs)
    if isinstance(output, tuple):
        stdout_b, stderr_b = output
    else:
        stdout_b, stderr_b = output, b""
    stdout = (stdout_b or b"").decode(errors="replace")
    stderr = (stderr_b or b"").decode(errors="replace")
    return exit_code, stdout, stderr


def _pg_admin_kwargs(container: DockerContainer) -> dict[str, Any]:
    from pytest_testcontainers._internal.port_resolver import normalize_host

    return {
        "host": normalize_host(container.get_container_host_ip()),
        "port": int(container.get_exposed_port(5432)),
        "user": container.username,
        "password": container.password,
        "dbname": "postgres",
    }


def _pg_create_via_psycopg(container: DockerContainer, name: str) -> None:
    import psycopg
    from psycopg import sql

    kwargs = _pg_admin_kwargs(container)
    try:
        with psycopg.connect(autocommit=True, **kwargs) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except Exception as exc:
        raise HybridFixtureError(
            f"CREATE DATABASE {name!r} failed via psycopg",
            command=f"CREATE DATABASE {name!r}",
            stderr=repr(exc),
            exit_code=None,
        ) from exc


def _pg_drop_via_psycopg(container: DockerContainer, name: str) -> None:
    import psycopg
    from psycopg import sql

    kwargs = _pg_admin_kwargs(container)
    try:
        with psycopg.connect(autocommit=True, **kwargs) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
    except Exception as exc:
        raise HybridFixtureError(
            f"DROP DATABASE {name!r} failed via psycopg",
            command=f"DROP DATABASE {name!r} WITH (FORCE)",
            stderr=repr(exc),
            exit_code=None,
        ) from exc


def _pg_run_in_container(container: DockerContainer, sql_text: str) -> None:
    argv = [
        "psql",
        "-U",
        container.username,
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql_text,
    ]
    env = {"PGPASSWORD": container.password}
    exit_code, stdout, stderr = _run_in_container(container, argv, env=env)
    if exit_code != 0:
        raise HybridFixtureError(
            f"psql admin command failed (exit={exit_code})",
            command=sql_text,
            stderr=(stderr or stdout).strip(),
            exit_code=exit_code,
        )


def create_postgres_db(container: DockerContainer, name: str) -> None:
    """Issue ``CREATE DATABASE <name>`` against the session container."""
    try:
        import psycopg  # noqa: F401
    except ImportError:
        _maybe_warn_fallback("tc_psql_db", "psycopg[binary]")
        _pg_run_in_container(container, f'CREATE DATABASE "{name}"')
        return
    _pg_create_via_psycopg(container, name)


def drop_postgres_db(container: DockerContainer, name: str) -> None:
    """Issue ``DROP DATABASE <name> WITH (FORCE)``."""
    try:
        import psycopg  # noqa: F401
    except ImportError:
        _pg_run_in_container(container, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        return
    _pg_drop_via_psycopg(container, name)


def _mysql_run_in_container(container: DockerContainer, sql_text: str) -> None:
    argv = [
        "mysql",
        f"-u{container.username}",
        f"-p{container.password}",
        "-e",
        sql_text,
    ]
    exit_code, stdout, stderr = _run_in_container(container, argv)
    if exit_code != 0:
        raise HybridFixtureError(
            f"mysql admin command failed (exit={exit_code})",
            command=sql_text,
            stderr=(stderr or stdout).strip(),
            exit_code=exit_code,
        )


def _mysql_run_pymysql(container: DockerContainer, sql_text: str) -> None:
    import pymysql

    from pytest_testcontainers._internal.port_resolver import normalize_host

    try:
        conn = pymysql.connect(
            host=normalize_host(container.get_container_host_ip()),
            port=int(container.get_exposed_port(3306)),
            user=container.username,
            password=container.password,
            autocommit=True,
        )
    except Exception as exc:
        raise HybridFixtureError(
            "mysql admin connect failed",
            command=sql_text,
            stderr=repr(exc),
            exit_code=None,
        ) from exc
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
    except Exception as exc:
        raise HybridFixtureError(
            "mysql admin command failed via pymysql",
            command=sql_text,
            stderr=repr(exc),
            exit_code=None,
        ) from exc
    finally:
        try:
            conn.close()
        except Exception as exc:
            logger.debug("pymysql conn.close() failed: %r", exc)


def create_mysql_db(container: DockerContainer, name: str) -> None:
    try:
        import pymysql  # noqa: F401
    except ImportError:
        _maybe_warn_fallback("tc_mysql_db", "pymysql")
        _mysql_run_in_container(container, f"CREATE DATABASE `{name}`")
        return
    _mysql_run_pymysql(container, f"CREATE DATABASE `{name}`")


def drop_mysql_db(container: DockerContainer, name: str) -> None:
    try:
        import pymysql  # noqa: F401
    except ImportError:
        _mysql_run_in_container(container, f"DROP DATABASE IF EXISTS `{name}`")
        return
    _mysql_run_pymysql(container, f"DROP DATABASE IF EXISTS `{name}`")


def _mongo_run_in_container_drop(container: DockerContainer, name: str) -> None:
    uri = (
        f"mongodb://{container.username}:{container.password}"
        f"@127.0.0.1:27017/admin?authSource=admin"
    )
    js = f"db.getSiblingDB({name!r}).dropDatabase()"
    argv = ["mongosh", "--quiet", uri, "--eval", js]
    exit_code, stdout, stderr = _run_in_container(container, argv)
    if exit_code != 0:
        raise HybridFixtureError(
            f"mongosh dropDatabase failed (exit={exit_code})",
            command=js,
            stderr=(stderr or stdout).strip(),
            exit_code=exit_code,
        )


def _mongo_run_pymongo_drop(container: DockerContainer, name: str) -> None:
    from pymongo import MongoClient

    from pytest_testcontainers._internal.port_resolver import normalize_host

    host = normalize_host(container.get_container_host_ip())
    port = int(container.get_exposed_port(27017))
    try:
        client = MongoClient(
            host=host,
            port=port,
            username=container.username,
            password=container.password,
            serverSelectionTimeoutMS=5000,
        )
        try:
            client.drop_database(name)
        finally:
            client.close()
    except Exception as exc:
        raise HybridFixtureError(
            "mongo dropDatabase failed via pymongo",
            command=f"dropDatabase({name!r})",
            stderr=repr(exc),
            exit_code=None,
        ) from exc


def drop_mongo_db(container: DockerContainer, name: str) -> None:
    """Drop a Mongo database. First write materializes it, so no create step."""
    try:
        import pymongo  # noqa: F401
    except ImportError:
        _maybe_warn_fallback("tc_mongo_db", "pymongo")
        _mongo_run_in_container_drop(container, name)
        return
    _mongo_run_pymongo_drop(container, name)


def flush_redis(container: DockerContainer) -> None:
    """Run ``redis-cli FLUSHALL`` inside the container (exec-only by design)."""
    argv = ["redis-cli", "FLUSHALL"]
    exit_code, stdout, stderr = _run_in_container(container, argv)
    if exit_code != 0:
        raise HybridFixtureError(
            f"redis-cli FLUSHALL failed (exit={exit_code})",
            command="redis-cli FLUSHALL",
            stderr=(stderr or stdout).strip(),
            exit_code=exit_code,
        )
