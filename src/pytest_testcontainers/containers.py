"""Reuse lookup, teardown registration, Ryuk-disable choreography.

Lives between the maker layer (which orchestrates lifecycle) and the
underlying ``testcontainers-python`` / ``docker-py`` libraries.

Teardown runs on three paths, in decreasing order of preference:

1. the maker's ``finally`` block (normal fixture teardown);
2. this module's ``SIGTERM``/``SIGHUP`` handler;
3. ``atexit`` (clean exit and ``KeyboardInterrupt``).

Ryuk is the fourth line only. It is worth keeping — nothing in-process
survives ``SIGKILL`` — but it must never be the *routine* mechanism: it
reaps ~10 s after the socket drops, and if the Docker daemon restarts in
the meantime it never reaps at all. Hence the signal handler, and hence
:func:`shutdown_ryuk` at the end of a session rather than letting the
reaper idle out on its own.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
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


def _load_reaper() -> Any | None:
    """Return testcontainers' ``Reaper`` class, or ``None`` if unavailable.

    Indirection exists so tests can substitute a double without reaching
    into ``testcontainers.core.container``.
    """
    try:
        from testcontainers.core.container import Reaper
    except ImportError:  # pragma: no cover — testcontainers is a hard dep
        return None
    return Reaper


def ryuk_maybe_running() -> bool:
    """True if this process may have started a Ryuk container.

    Lets ``pytest_unconfigure`` skip :func:`shutdown_ryuk` — and with it the
    import of ``testcontainers.core.container`` — on the many sessions that
    never touch Docker at all. Reuse mode disables Ryuk before the first
    ``.start()``, and those containers are never registered for teardown, so
    "something was registered" is exactly the condition we want.
    """
    with _teardown_lock:
        return _ryuk_possibly_started


def shutdown_ryuk() -> None:
    """Stop and remove the Ryuk reaper container for this process.

    testcontainers-python starts one Ryuk per process (so: one per xdist
    worker) and never shuts it down — it just idles until
    ``RYUK_RECONNECTION_TIMEOUT`` elapses after the socket drops, which
    measured at ~60 s of a privileged container holding an rw bind-mount
    of the Docker socket, per worker. Once our own teardown has run there
    is nothing left for it to reap, so we close it out explicitly.

    Idempotent within a generation: :func:`register_atexit_stop` re-arms
    it, because a later ``.start()`` builds a fresh ``Reaper``.
    """
    global _ryuk_shutdown_done
    with _teardown_lock:
        if _ryuk_shutdown_done:
            return
        _ryuk_shutdown_done = True
    reaper = _load_reaper()
    if reaper is None:
        return
    try:
        reaper.delete_instance()
    except Exception:
        # Never propagate: this runs from signal handlers and atexit,
        # where raising would mask the real reason we are shutting down.
        logger.exception("cleanup: could not shut down the Ryuk reaper")


# --------------------------------------------------------------------------
# Teardown registry — drained by signal handlers and by atexit
# --------------------------------------------------------------------------
# Re-entrant: the signal handler can fire while a registration holds the lock.
_teardown_lock = threading.RLock()
_teardown_callbacks: dict[int, Any] = {}  # id(container) -> bound callback
_ryuk_shutdown_done: bool = False
# Sticky, unlike the registry: deregistration empties the callbacks but the
# reaper is still up and still ours to close.
_ryuk_possibly_started: bool = False

# SIGINT is deliberately absent: Python turns it into KeyboardInterrupt,
# pytest unwinds normally and the atexit net already covers it. Installing a
# handler here would break that unwinding for no gain.
TEARDOWN_SIGNALS: tuple[int, ...] = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP", "SIGBREAK") if hasattr(signal, name)
)

_signal_handlers_installed: bool = False
_prev_signal_handlers: dict[int, Any] = {}


def _stop_container(container: DockerContainer) -> None:
    """Stop+remove ``container``, tolerating an already-removed one."""
    try:
        container.stop()
    except Exception as exc:
        # Common case: normal teardown already stopped+removed the
        # container, so a Docker NotFound is expected and silent.
        # Anything else is logged, never re-raised — we run from signal
        # handlers and atexit, where an exception is discarded anyway and
        # would only mask the reason we are tearing down. Per CLAUDE.md,
        # the narrowed branch + log make the rationale auditable.
        try:
            from docker.errors import NotFound
        except ImportError:  # pragma: no cover
            NotFound = ()  # type: ignore[assignment]
        if not isinstance(exc, NotFound):
            logger.exception("cleanup: container.stop() failed")


def _make_teardown_stop(container: DockerContainer) -> Any:
    def _cb() -> None:
        _stop_container(container)

    return _cb


def register_atexit_stop(container: DockerContainer) -> None:
    """Arm every teardown path for ``container`` (atexit + signals).

    Name kept for backwards compatibility — ``pytest-testcontainers-django``
    imports it — but it now also installs the signal handlers, so a
    ``SIGTERM``d run removes its containers instead of leaving them for Ryuk.
    """
    global _ryuk_shutdown_done, _ryuk_possibly_started
    key = id(container)
    with _teardown_lock:
        # A fresh container means a fresh Ryuk may be started, so re-arm.
        _ryuk_shutdown_done = False
        _ryuk_possibly_started = True
        if key in _teardown_callbacks:
            return
        cb = _make_teardown_stop(container)
        _teardown_callbacks[key] = cb
    atexit.register(cb)
    _install_signal_handlers()


def deregister_atexit_stop(container: DockerContainer) -> None:
    """Remove ``container`` from the teardown paths after normal cleanup."""
    key = id(container)
    with _teardown_lock:
        cb = _teardown_callbacks.pop(key, None)
        empty = not _teardown_callbacks
    if cb is not None:
        atexit.unregister(cb)
    if empty:
        # Nothing left to protect — stop intercepting the process's signals.
        _restore_signal_handlers()


def _run_all_teardowns() -> None:
    with _teardown_lock:
        callbacks = list(_teardown_callbacks.values())
    for cb in callbacks:
        try:
            cb()
        except Exception:
            logger.exception("cleanup: teardown callback failed")


# --------------------------------------------------------------------------
# Signal handling
# --------------------------------------------------------------------------
def _install_signal_handlers() -> None:
    """Intercept ``SIGTERM``/``SIGHUP`` for as long as containers are live.

    No-op off the main thread (``signal.signal`` is main-thread only) and
    no-op for any signal the process was already ignoring — ``nohup`` sets
    ``SIGHUP`` to ``SIG_IGN`` precisely so the run survives it, and
    overriding that would turn a survivable hangup into a kill.
    """
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in TEARDOWN_SIGNALS:
        try:
            previous = signal.getsignal(signum)
        except (ValueError, OSError):  # pragma: no cover — platform-specific
            continue
        if previous is signal.SIG_IGN:
            continue
        try:
            signal.signal(signum, _signal_teardown)
        except (ValueError, OSError):  # pragma: no cover — platform-specific
            logger.debug("could not install teardown handler for signal %s", signum)
            continue
        _prev_signal_handlers[signum] = previous
    _signal_handlers_installed = bool(_prev_signal_handlers)


def _restore_signal_handlers() -> None:
    global _signal_handlers_installed
    if not _signal_handlers_installed:
        return
    if threading.current_thread() is not threading.main_thread():  # pragma: no cover
        return
    for signum, previous in list(_prev_signal_handlers.items()):
        try:
            signal.signal(signum, previous)
        except (ValueError, OSError):  # pragma: no cover — platform-specific
            logger.debug("could not restore handler for signal %s", signum)
    _prev_signal_handlers.clear()
    _signal_handlers_installed = False


def _reraise_signal(signum: int) -> None:
    """Deliver ``signum`` again, now that the default disposition is back."""
    os.kill(os.getpid(), signum)


def _signal_teardown(signum: int, frame: Any) -> None:
    """Remove our containers, then let the signal take its normal course."""
    _run_all_teardowns()
    shutdown_ryuk()

    previous = _prev_signal_handlers.get(signum, signal.SIG_DFL)
    # Restore before doing anything that can re-enter, so a second signal
    # during a slow teardown terminates instead of recursing.
    _restore_signal_handlers()

    if previous is signal.SIG_IGN:  # pragma: no cover — never installed over SIG_IGN
        return
    if callable(previous):
        previous(signum, frame)
        return
    _reraise_signal(signum)


def _reset_teardown_state_for_tests() -> None:
    """Test hook: drop the registry and un-hook the process's signals."""
    global _ryuk_shutdown_done, _ryuk_possibly_started
    _restore_signal_handlers()
    with _teardown_lock:
        for cb in _teardown_callbacks.values():
            atexit.unregister(cb)
        _teardown_callbacks.clear()
        _ryuk_shutdown_done = False
        _ryuk_possibly_started = False


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
