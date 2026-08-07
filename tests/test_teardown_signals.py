"""Deterministic teardown: signal handlers + explicit Ryuk shutdown.

``pytest_unconfigure`` and ``atexit`` cover a clean exit and Ctrl-C, but
neither runs on ``SIGTERM``/``SIGHUP`` — the interpreter dies before the
handlers fire and the containers survive until Ryuk notices the dropped
socket (measured: ~10 s). These tests pin the behaviour that closes that
window: registered containers are stopped from inside the signal handler,
Ryuk is torn down explicitly, and the original signal disposition is
restored so exit status semantics stay intact.
"""

from __future__ import annotations

import signal
import threading

import pytest

from pytest_testcontainers import containers


class _FakeContainer:
    """Stands in for a testcontainers ``DockerContainer``."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.stop_calls = 0
        self._raises = raises

    def stop(self) -> None:
        self.stop_calls += 1
        if self._raises is not None:
            raise self._raises


# --------------------------------------------------------------------------
# shutdown_ryuk
# --------------------------------------------------------------------------
def test_shutdown_ryuk_calls_reaper_delete_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Reaper:
        @classmethod
        def delete_instance(cls) -> None:
            calls.append("deleted")

    monkeypatch.setattr(containers, "_load_reaper", lambda: _Reaper)
    containers.shutdown_ryuk()
    assert calls == ["deleted"]


def test_shutdown_ryuk_is_noop_when_reaper_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(containers, "_load_reaper", lambda: None)
    containers.shutdown_ryuk()  # must not raise


def test_shutdown_ryuk_logs_and_swallows_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Reaper:
        @classmethod
        def delete_instance(cls) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(containers, "_load_reaper", lambda: _Reaper)
    with caplog.at_level("ERROR", logger="pytest_testcontainers"):
        containers.shutdown_ryuk()
    assert "boom" in caplog.text


def test_ryuk_maybe_running_is_false_until_a_container_is_registered() -> None:
    """Sessions that never touch Docker must not import testcontainers."""
    assert containers.ryuk_maybe_running() is False
    containers.register_atexit_stop(_FakeContainer())
    assert containers.ryuk_maybe_running() is True


def test_ryuk_maybe_running_stays_true_after_deregistration() -> None:
    """The reaper outlives the container it was started for."""
    container = _FakeContainer()
    containers.register_atexit_stop(container)
    containers.deregister_atexit_stop(container)
    assert containers.ryuk_maybe_running() is True


def test_unconfigure_skips_ryuk_when_nothing_started(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytest_testcontainers import plugin

    monkeypatch.setattr(
        containers, "_load_reaper", lambda: pytest.fail("testcontainers imported needlessly")
    )
    plugin.pytest_unconfigure(None)


def test_unconfigure_shuts_ryuk_down_after_a_container_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pytest_testcontainers import plugin

    calls: list[str] = []

    class _Reaper:
        @classmethod
        def delete_instance(cls) -> None:
            calls.append("deleted")

    containers.register_atexit_stop(_FakeContainer())
    monkeypatch.setattr(containers, "_load_reaper", lambda: _Reaper)
    plugin.pytest_unconfigure(None)
    assert calls == ["deleted"]


def test_shutdown_ryuk_runs_once_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call is a no-op — unconfigure and atexit both invoke it."""
    calls: list[str] = []

    class _Reaper:
        @classmethod
        def delete_instance(cls) -> None:
            calls.append("deleted")

    monkeypatch.setattr(containers, "_load_reaper", lambda: _Reaper)
    containers.shutdown_ryuk()
    containers.shutdown_ryuk()
    assert calls == ["deleted"]


# --------------------------------------------------------------------------
# Signal handler installation
# --------------------------------------------------------------------------
def test_registering_a_container_installs_signal_handlers() -> None:
    container = _FakeContainer()
    containers.register_atexit_stop(container)
    for signum in containers.TEARDOWN_SIGNALS:
        assert signal.getsignal(signum) is containers._signal_teardown


def test_signal_handlers_are_installed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The saved "previous handler" must not be overwritten by our own."""
    containers.register_atexit_stop(_FakeContainer())
    saved = dict(containers._prev_signal_handlers)
    containers.register_atexit_stop(_FakeContainer())
    assert containers._prev_signal_handlers == saved
    assert containers._signal_teardown not in saved.values()


def test_signal_handlers_not_installed_off_main_thread() -> None:
    """``signal.signal`` only works on the main thread; xdist/execnet is not."""
    installed: list[bool] = []

    def _worker() -> None:
        containers.register_atexit_stop(_FakeContainer())
        installed.append(containers._signal_handlers_installed)

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    assert installed == [False]


# --------------------------------------------------------------------------
# Signal handler behaviour
# --------------------------------------------------------------------------
def test_signal_teardown_stops_registered_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = _FakeContainer(), _FakeContainer()
    containers.register_atexit_stop(first)
    containers.register_atexit_stop(second)

    reraised: list[int] = []
    monkeypatch.setattr(containers, "_reraise_signal", lambda signum: reraised.append(signum))
    monkeypatch.setattr(containers, "_load_reaper", lambda: None)

    containers._signal_teardown(signal.SIGTERM, None)

    assert first.stop_calls == 1
    assert second.stop_calls == 1
    assert reraised == [signal.SIGTERM]


def test_signal_teardown_shuts_ryuk_down_after_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ryuk must die last — it is the net for anything our stop() missed."""
    order: list[str] = []

    class _Recorder:
        def stop(self) -> None:
            order.append("container")

    class _Reaper:
        @classmethod
        def delete_instance(cls) -> None:
            order.append("ryuk")

    containers.register_atexit_stop(_Recorder())
    monkeypatch.setattr(containers, "_load_reaper", lambda: _Reaper)
    monkeypatch.setattr(containers, "_reraise_signal", lambda signum: None)

    containers._signal_teardown(signal.SIGTERM, None)

    assert order == ["container", "ryuk"]


def test_signal_teardown_continues_after_a_failing_stop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    broken = _FakeContainer(raises=RuntimeError("cannot stop"))
    healthy = _FakeContainer()
    containers.register_atexit_stop(broken)
    containers.register_atexit_stop(healthy)
    monkeypatch.setattr(containers, "_reraise_signal", lambda signum: None)
    monkeypatch.setattr(containers, "_load_reaper", lambda: None)

    with caplog.at_level("ERROR", logger="pytest_testcontainers"):
        containers._signal_teardown(signal.SIGTERM, None)

    assert healthy.stop_calls == 1
    assert "cannot stop" in caplog.text


def test_signal_teardown_chains_a_previous_callable_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler installed by the host application still gets to run."""
    seen: list[int] = []

    def _previous(signum: int, frame: object) -> None:
        seen.append(signum)

    signal.signal(signal.SIGTERM, _previous)
    try:
        containers.register_atexit_stop(_FakeContainer())
        monkeypatch.setattr(containers, "_load_reaper", lambda: None)
        containers._signal_teardown(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    assert seen == [signal.SIGTERM]


def test_signal_teardown_restores_default_disposition(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIG_DFL must be reinstated before re-raising, or we loop forever."""
    containers.register_atexit_stop(_FakeContainer())
    monkeypatch.setattr(containers, "_load_reaper", lambda: None)

    handler_at_reraise: list[object] = []
    monkeypatch.setattr(
        containers,
        "_reraise_signal",
        lambda signum: handler_at_reraise.append(signal.getsignal(signum)),
    )

    containers._signal_teardown(signal.SIGTERM, None)
    assert handler_at_reraise == [signal.SIG_DFL]


def test_deregistering_last_container_restores_handlers() -> None:
    """A finished session must not leave our handler wired into the process."""
    container = _FakeContainer()
    containers.register_atexit_stop(container)
    containers.deregister_atexit_stop(container)
    for signum in containers.TEARDOWN_SIGNALS:
        assert signal.getsignal(signum) is not containers._signal_teardown
