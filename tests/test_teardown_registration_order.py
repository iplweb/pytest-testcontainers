"""Teardown must be armed *before* the container is started, not after.

`.start()` does not return until the wait strategy says the service is
ready. For a Postgres seeded from an init script that is many seconds —
and it is exactly the moment a developer watching a wedged machine
reaches for `kill`. Arming afterwards leaves that whole window
unprotected: the container exists, nothing knows about it, and the run
dies without removing it.

`DockerContainer.stop()` is a no-op while `_container` is None, so
registering early is safe even if the signal lands before Docker has
created anything.
"""

from __future__ import annotations

import pytest

from pytest_testcontainers import makers


class _FakeContainer:
    def __init__(self, **kwargs: object) -> None:
        self._container = None
        self.name: str | None = None

    def with_name(self, name: str) -> _FakeContainer:
        self.name = name
        return self

    def with_env(self, key: str, value: str) -> _FakeContainer:
        return self

    def stop(self) -> None:
        pass


@pytest.fixture
def _traced(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> list[str]:
    order: list[str] = []
    monkeypatch.setattr(makers, "check_docker_daemon", lambda: None)
    monkeypatch.setattr(makers, "register_atexit_stop", lambda c: order.append("armed"))
    monkeypatch.setattr(makers, "deregister_atexit_stop", lambda c: order.append("disarmed"))
    monkeypatch.setattr(makers, "start_or_raise", lambda c, image: order.append("started"))
    return order


def test_teardown_is_armed_before_start(_traced: list[str]) -> None:
    with makers.make_container(_FakeContainer):
        pass
    assert _traced.index("armed") < _traced.index("started"), (
        f"container started before teardown was armed: {_traced}"
    )


def test_teardown_stays_armed_when_start_fails(
    monkeypatch: pytest.MonkeyPatch, _traced: list[str]
) -> None:
    """A container that failed its wait strategy still exists in Docker."""

    def _boom(container: object, image: str) -> None:
        _traced.append("started")
        raise RuntimeError("wait strategy timed out")

    monkeypatch.setattr(makers, "start_or_raise", _boom)

    with pytest.raises(RuntimeError), makers.make_container(_FakeContainer):
        pass

    assert "armed" in _traced
    assert "disarmed" not in _traced


def test_reuse_mode_never_arms_teardown(_traced: list[str], monkeypatch) -> None:
    """Reuse containers must survive the run — and the signal handler."""
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")
    monkeypatch.setattr(makers, "find_existing_container", lambda name: None)
    monkeypatch.setattr(makers, "disable_ryuk_once", lambda: None)

    with makers.make_container(_FakeContainer):
        pass

    assert "armed" not in _traced
