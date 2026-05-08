"""Reuse lookup, atexit registration, Ryuk-disable choreography.

Lives between the maker layer (which orchestrates lifecycle) and the
underlying ``testcontainers-python`` / ``docker-py`` libraries.
"""

from __future__ import annotations

import atexit
import logging
import threading
import traceback
from typing import TYPE_CHECKING, Any

from pytest_testcontainers.errors import (
    ContainerStartError,
    ReuseConflictError,
)

if TYPE_CHECKING:
    from testcontainers.core.container import DockerContainer

logger = logging.getLogger("pytest_testcontainers")

# --------------------------------------------------------------------------
# Ryuk disable — idempotent, lazy
# --------------------------------------------------------------------------
_ryuk_lock = threading.Lock()
_ryuk_disabled: bool = False


def disable_ryuk_once() -> None:
    """Flip ``testcontainers_config.ryuk_disabled = True`` exactly once.

    Per §8.5: testcontainers-python does not spawn the Ryuk reaper at
    import time; the flag must just be set before the first container's
    ``.start()``. We do it lazily inside the maker so module import has
    no side effects.
    """
    global _ryuk_disabled
    with _ryuk_lock:
        if _ryuk_disabled:
            return
        try:
            from testcontainers.core.config import testcontainers_config
        except ImportError:  # pragma: no cover — testcontainers is a hard dep
            return
        testcontainers_config.ryuk_disabled = True
        _ryuk_disabled = True


# --------------------------------------------------------------------------
# atexit safety net — only fires when pytest_unconfigure is skipped
# --------------------------------------------------------------------------
_atexit_lock = threading.Lock()
_atexit_callbacks: dict[int, Any] = {}  # id(container) -> bound callback


def _make_atexit_stop(container: DockerContainer) -> Any:
    def _cb() -> None:
        try:
            container.stop()
        except Exception as exc:
            # Common case: normal teardown already stopped+removed the
            # container, so a Docker NotFound is expected and silent.
            # Anything else gets printed (no re-raise — interpreter
            # shutdown would discard the exception anyway). Per CLAUDE.md,
            # this is the one suppress-exception pattern allowed; the
            # narrowed branch + log make the rationale auditable.
            try:
                from docker.errors import NotFound
            except ImportError:  # pragma: no cover
                NotFound = ()  # type: ignore[assignment]
            if not isinstance(exc, NotFound):
                traceback.print_exc()

    return _cb


def register_atexit_stop(container: DockerContainer) -> None:
    """Register an atexit callback that stops ``container`` exactly once."""
    key = id(container)
    with _atexit_lock:
        if key in _atexit_callbacks:
            return
        cb = _make_atexit_stop(container)
        _atexit_callbacks[key] = cb
    atexit.register(cb)


def deregister_atexit_stop(container: DockerContainer) -> None:
    """Remove the atexit callback for ``container`` after normal cleanup."""
    key = id(container)
    with _atexit_lock:
        cb = _atexit_callbacks.pop(key, None)
    if cb is not None:
        atexit.unregister(cb)


# --------------------------------------------------------------------------
# Reuse-by-name lookup
# --------------------------------------------------------------------------
def find_existing_container(name: str) -> Any | None:
    """Return the docker-py container object for ``name``, or None.

    "Found" includes any state — the caller decides what to do based on
    ``container.status`` (``"running"``, ``"exited"``, ``"created"``,
    ``"paused"``, …).
    """
    import docker
    from docker.errors import NotFound

    client = docker.from_env()
    try:
        container = client.containers.get(name)
    except NotFound:
        return None
    return container


def restart_stopped_or_raise(docker_container: Any, name: str) -> None:
    """Bring a stopped/created/paused container back to running.

    Raises :class:`ReuseConflictError` if Docker refuses to start it
    (typically because the previously-mapped host port is now taken).
    """
    try:
        docker_container.start()
        docker_container.reload()
    except Exception as exc:
        raise ReuseConflictError(f"cannot start existing container {name!r}: {exc!r}") from exc


def bind_to_running(
    container_cls: type[DockerContainer],
    docker_container: Any,
    *constructor_args: Any,
    **constructor_kwargs: Any,
) -> DockerContainer:
    """Build a ``testcontainers-python`` instance bound to a running container.

    The instance is constructed but ``.start()`` is *not* called — instead
    we set the private attributes that ``.start()`` would normally set
    so that ``.get_container_host_ip()`` / ``.get_exposed_port()`` work
    against the existing container.
    """
    instance = container_cls(*constructor_args, **constructor_kwargs)
    # testcontainers-python's DockerContainer keeps the live wrapper as
    # `_container`. After `.start()`, this attribute is the docker-py
    # Container object; before, it's None.
    instance._container = docker_container
    return instance


# --------------------------------------------------------------------------
# Convenience wrapper: wrap a .start() call so failures convert cleanly
# --------------------------------------------------------------------------
def start_or_raise(container: DockerContainer, image_for_msg: str) -> None:
    """Call ``container.start()`` and convert failures to ``ContainerStartError``."""
    try:
        container.start()
    except Exception as exc:
        log_tail = ""
        try:
            raw = container.get_logs() if hasattr(container, "get_logs") else None
            if raw:
                # testcontainers' get_logs returns (stdout, stderr) bytes
                stdout = raw[0] if isinstance(raw, tuple) else raw
                if isinstance(stdout, bytes):
                    log_tail = stdout.decode(errors="replace")
                else:
                    log_tail = str(stdout)
                # last 50 lines
                log_tail = "\n".join(log_tail.splitlines()[-50:])
        except Exception as log_exc:
            logger.debug("could not retrieve container logs: %r", log_exc)

        message = f"failed to start container (image={image_for_msg!r}): {exc!r}"
        if log_tail:
            message = f"{message}\n--- last 50 log lines ---\n{log_tail}"
        raise ContainerStartError(message) from exc
