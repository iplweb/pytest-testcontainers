"""``--testcontainers-clean`` selection rules.

The old implementation matched the ``<project>-tc-`` name prefix only, so
it could never reach two of the three things that actually pile up on a
dev box: Ryuk containers (named ``testcontainers-ryuk-<uuid>``) and the
containers ``pytest-testcontainers-django`` used to start unnamed. Both
carry the ``org.testcontainers=true`` label, so the label is the reliable
selector; the prefix stays as a fallback for reuse containers whose Ryuk
was deliberately never started.
"""

from __future__ import annotations

import pytest

from pytest_testcontainers import plugin


class _FakeContainer:
    def __init__(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.name = name
        self.labels = labels or {}
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeClient:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self._containers = containers

    class _Collection:
        def __init__(self, containers: list[_FakeContainer]) -> None:
            self._containers = containers

        def list(self, all: bool = False) -> list[_FakeContainer]:
            return list(self._containers)

    @property
    def containers(self) -> _Collection:
        return self._Collection(self._containers)


_TC_LABEL = {"org.testcontainers": "true"}


def _names(selected: list[_FakeContainer]) -> list[str]:
    return [c.name for c in selected]


def test_selects_containers_by_testcontainers_label() -> None:
    """django-plugin containers have random Docker names but carry the label."""
    client = _FakeClient(
        [
            _FakeContainer("goofy_torvalds", _TC_LABEL),
            _FakeContainer("some-unrelated-app"),
        ]
    )
    selected = plugin._select_containers(client, prefix="myproj-tc-", project_only=False)
    assert _names(selected) == ["goofy_torvalds"]


def test_selects_ryuk_containers() -> None:
    client = _FakeClient([_FakeContainer("testcontainers-ryuk-2c5f-41ab")])
    selected = plugin._select_containers(client, prefix="myproj-tc-", project_only=False)
    assert _names(selected) == ["testcontainers-ryuk-2c5f-41ab"]


def test_selects_reuse_containers_by_prefix_without_label() -> None:
    """Reuse mode disables Ryuk, so those containers may carry no label."""
    client = _FakeClient([_FakeContainer("myproj-tc-psql-main-ab12-master")])
    selected = plugin._select_containers(client, prefix="myproj-tc-", project_only=False)
    assert _names(selected) == ["myproj-tc-psql-main-ab12-master"]


def test_leaves_unrelated_containers_alone() -> None:
    """A docker-compose stack must survive even the broad sweep."""
    client = _FakeClient(
        [
            _FakeContainer("bpp-db-1", {"com.docker.compose.project": "bpp"}),
            _FakeContainer("otherproj-tc-psql-main-cd34-master"),
        ]
    )
    selected = plugin._select_containers(client, prefix="myproj-tc-", project_only=False)
    assert _names(selected) == []


def test_broad_sweep_reaches_another_projects_testcontainers() -> None:
    """Documented (and chosen) behaviour: the label wins over project scope.

    This is the destructive edge — on a shared host it also hits a
    concurrently running session, which is what --testcontainers-clean-project
    exists for.
    """
    other = _FakeContainer("otherproj-tc-psql-main-cd34-master", _TC_LABEL)
    client = _FakeClient([other])
    assert _names(plugin._select_containers(client, prefix="myproj-tc-", project_only=False)) == [
        other.name
    ]
    assert plugin._select_containers(client, prefix="myproj-tc-", project_only=True) == []


def test_project_only_ignores_labels_and_ryuk() -> None:
    """The narrow mode is the escape hatch on a shared/parallel host."""
    client = _FakeClient(
        [
            _FakeContainer("myproj-tc-psql-main-ab12-master"),
            _FakeContainer("goofy_torvalds", _TC_LABEL),
            _FakeContainer("testcontainers-ryuk-2c5f-41ab"),
        ]
    )
    selected = plugin._select_containers(client, prefix="myproj-tc-", project_only=True)
    assert _names(selected) == ["myproj-tc-psql-main-ab12-master"]


def test_each_container_selected_once() -> None:
    """A container can match prefix *and* label — it must not be removed twice."""
    client = _FakeClient([_FakeContainer("myproj-tc-psql-main-ab12-master", _TC_LABEL)])
    selected = plugin._select_containers(client, prefix="myproj-tc-", project_only=False)
    assert len(selected) == 1


class _StubConfig:
    def __init__(self, options: dict[str, object]) -> None:
        self._options = options

    def getoption(self, name: str):
        return self._options.get(name)


def test_cmdline_main_is_inert_without_the_flag() -> None:
    config = _StubConfig({"testcontainers_clean": False, "testcontainers_clean_project": False})
    assert plugin.pytest_cmdline_main(config) is None


def test_cmdline_main_removes_selected_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doomed = _FakeContainer("goofy_torvalds", _TC_LABEL)
    spared = _FakeContainer("bpp-db-1")
    monkeypatch.setattr(plugin, "_docker_client", lambda: _FakeClient([doomed, spared]))

    config = _StubConfig({"testcontainers_clean": True, "testcontainers_clean_project": False})
    assert plugin.pytest_cmdline_main(config) == 0
    assert doomed.removed is True
    assert spared.removed is False
    assert "goofy_torvalds" in capsys.readouterr().out


def test_cmdline_main_reports_failures_without_aborting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Stubborn(_FakeContainer):
        def remove(self, force: bool = False) -> None:
            raise RuntimeError("device busy")

    stubborn = _Stubborn("testcontainers-ryuk-dead", _TC_LABEL)
    healthy = _FakeContainer("goofy_torvalds", _TC_LABEL)
    monkeypatch.setattr(plugin, "_docker_client", lambda: _FakeClient([stubborn, healthy]))

    config = _StubConfig({"testcontainers_clean": True, "testcontainers_clean_project": False})
    assert plugin.pytest_cmdline_main(config) == 0
    assert healthy.removed is True
    captured = capsys.readouterr()
    assert "device busy" in captured.err
