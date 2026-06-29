"""Docker daemon health check — single source of truth for daemon ping.

The cache stores **only successful pings**. A successful ping flips a
process-wide flag; subsequent maker calls in the same process skip the
probe. A failed ping does NOT poison the cache, so a transient daemon
restart inside a long-running pytest session does not permanently
disable the plugin.
"""

from __future__ import annotations

import logging
import os
import threading

from pytest_testcontainers.errors import DockerNotRunningError

logger = logging.getLogger("pytest_testcontainers")

_lock = threading.Lock()
_ping_ok: bool = False


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def reset_cache() -> None:
    """Reset the success cache. Used in tests."""
    global _ping_ok
    with _lock:
        _ping_ok = False


def ensure_docker_host_env() -> None:
    """Export ``DOCKER_HOST`` from the active ``docker context`` when unset.

    ``docker.from_env()`` — used by docker-py, by testcontainers' own client,
    and by Ryuk's socket bind-mount — honors ``DOCKER_HOST`` but, unlike the
    ``docker`` CLI, ignores the active ``docker context``. On OrbStack /
    colima / a stopped Docker Desktop the daemon lives on a non-default socket
    and ``/var/run/docker.sock`` is absent or a dangling symlink, so container
    launch crashes with ``FileNotFoundError`` even though ``docker ps`` works.

    Exporting ``DOCKER_HOST`` once — before any client is built — makes the
    whole process resolve Docker the way the CLI does. No-op when
    ``DOCKER_HOST`` is already set (an explicit choice always wins) or when no
    context endpoint resolves (fall back to the platform default socket).
    """
    if os.environ.get("DOCKER_HOST"):
        return
    ctx = _active_docker_context()
    host = getattr(ctx, "Host", None) if ctx is not None else None
    if host:
        os.environ["DOCKER_HOST"] = host


def _active_docker_context():  # type: ignore[no-untyped-def]
    """Return the active ``docker context`` object, or ``None``.

    Resolution failures (older SDK without context support, malformed
    ``~/.docker/config.json``, missing context) are logged and swallowed so
    the caller falls back to the platform default socket.
    """
    try:
        from docker.context import ContextAPI

        return ContextAPI.get_current_context()
    except Exception:
        logger.debug("could not resolve active docker context", exc_info=True)
        return None


def check_docker_daemon() -> None:
    """Ping the Docker daemon once per process (on success).

    Raises :class:`DockerNotRunningError` (chained from the underlying
    docker-py exception) if the daemon cannot be reached.
    """
    global _ping_ok
    if _ping_ok:
        return
    # Honor the active `docker context` before any client (ours, testcontainers'
    # or Ryuk's) is built — even when the ping itself is skipped, so the launch
    # path still resolves the right socket on OrbStack / colima / Docker Desktop.
    ensure_docker_host_env()
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
