"""Maker naming with the daemon ping and start mocked out (no real Docker)."""

from __future__ import annotations

import re

import pytest

from pytest_testcontainers import makers
from pytest_testcontainers.makers import make_container
from pytest_testcontainers.reuse import container_name_for


class _Fake:
    def __init__(self, *args, **kwargs) -> None:
        self.assigned_name: str | None = None

    def with_name(self, name):
        self.assigned_name = name
        return self

    def with_env(self, *args, **kwargs):
        return self

    def stop(self):
        pass


@pytest.fixture
def _stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(makers, "check_docker_daemon", lambda: None)
    monkeypatch.setattr(makers, "start_or_raise", lambda instance, image: None)


def test_default_mode_assigns_templated_name(
    _stub_runtime, tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    captured: dict[str, str] = {}

    class Fake(_Fake):
        def with_name(self, name):
            captured["name"] = name
            return super().with_name(name)

    with make_container(Fake):
        pass

    name = captured["name"]
    assert "-tc-fake-" in name  # service slug derived from the class name
    assert re.search(r"-[0-9a-f]{4}$", name)  # default mode appends a rand token


def test_reuse_mode_assigns_deterministic_name(
    _stub_runtime, tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")
    monkeypatch.setattr(makers, "find_existing_container", lambda name: None)
    monkeypatch.setattr(makers, "disable_ryuk_once", lambda: None)
    captured: dict[str, str] = {}

    class Fake(_Fake):
        def with_name(self, name):
            captured["name"] = name
            return super().with_name(name)

    expected = container_name_for("fake", reuse_mode=True)
    with make_container(Fake):
        pass

    assert captured["name"] == expected
