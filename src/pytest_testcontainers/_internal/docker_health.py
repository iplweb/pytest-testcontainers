"""Docker daemon health check — single source of truth for daemon ping.

The cache stores **only successful pings**. A successful ping flips a
process-wide flag; subsequent maker calls in the same process skip the
probe. A failed ping does NOT poison the cache, so a transient daemon
restart inside a long-running pytest session does not permanently
disable the plugin.
"""

from __future__ import annotations

import os
import threading

from pytest_testcontainers.errors import DockerNotRunningError

_lock = threading.Lock()
_ping_ok: bool = False


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def reset_cache() -> None:
    """Reset the success cache. Used in tests."""
    global _ping_ok
    with _lock:
        _ping_ok = False


def check_docker_daemon() -> None:
    """Ping the Docker daemon once per process (on success).

    Raises :class:`DockerNotRunningError` (chained from the underlying
    docker-py exception) if the daemon cannot be reached.
    """
    global _ping_ok
    if _ping_ok:
        return
    if _is_truthy(os.environ.get("PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK")):
        return

    try:
        import docker  # local import: never trigger at module-import time
    except ImportError as exc:  # pragma: no cover — docker is a hard dep
        raise DockerNotRunningError(
            "docker-py is not installed; cannot ping Docker daemon"
        ) from exc

    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        # Surface the underlying daemon failure with a concrete cause.
        raise DockerNotRunningError(f"docker.from_env().ping() failed: {exc!r}") from exc

    with _lock:
        _ping_ok = True
