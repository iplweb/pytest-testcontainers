"""Maker behavior with the daemon ping mocked out (no real Docker required)."""

from __future__ import annotations

import docker
import pytest

from pytest_testcontainers.errors import DockerNotRunningError, PytestTestcontainersError
from pytest_testcontainers.makers import make_postgres


def test_maker_propagates_docker_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patching ``docker.from_env`` is enough — the daemon ping runs first."""

    class _Broken:
        def ping(self):
            raise ConnectionError("broken pipe")

    monkeypatch.setattr(docker, "from_env", lambda: _Broken())
    with pytest.raises(DockerNotRunningError), make_postgres() as _pg:
        pass


def test_dockernotrunning_is_pluginerror() -> None:
    assert issubclass(DockerNotRunningError, PytestTestcontainersError)
