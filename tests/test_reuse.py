"""Reuse-name resolution: project-name precedence, sanitization, composition."""

from __future__ import annotations

import textwrap

import pytest

from pytest_testcontainers import reuse
from pytest_testcontainers.reuse import (
    compose_name,
    is_plugin_disabled,
    is_reuse_enabled,
    project_name,
    reuse_name_for,
    sanitize_component,
    worker_id,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MyProject", "myproject"),
        ("my_project", "my_project"),
        ("my.project", "my.project"),
        ("my project", "my-project"),
        ("foo/bar", "foo-bar"),
        ("_internal", "internal"),
        ("123abc", "123abc"),
        ("--", "x"),
        ("acme.corp/widget", "acme.corp-widget"),
        ("...trailing.", "trailing"),
    ],
)
def test_sanitize_component(raw: str, expected: str) -> None:
    assert sanitize_component(raw) == expected


def test_compose_name_short() -> None:
    assert compose_name("myproject", "psql", "master") == "myproject-tc-psql-master"
    assert compose_name("myproject", "redis", "gw0") == "myproject-tc-redis-gw0"


def test_compose_name_truncates_when_long() -> None:
    long_project = "x" * 300
    out = compose_name(long_project, "psql", "master")
    assert len(out) <= 255
    assert out.startswith("x")
    assert "-tc-" not in out[-30:] or out.endswith(
        ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f")
    )


def test_worker_id_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert worker_id() == "master"


def test_worker_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    assert worker_id() == "gw3"


def test_project_name_cli_wins(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "from-env")
    reuse.set_cli_project("from-cli")
    assert project_name() == "from-cli"


def test_project_name_env_wins_over_pyproject(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "from-pyproject"
        """)
    )
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "from-env")
    assert project_name() == "from-env"


def test_project_name_from_pyproject(tmp_cwd) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "from-pyproject"
        """)
    )
    assert project_name() == "from-pyproject"


def test_project_name_walks_up_to_parent(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "outer-project"
        """)
    )
    sub = tmp_cwd / "sub" / "deeper"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert project_name() == "outer-project"


def test_project_name_fallback_emits_warning(tmp_cwd, capsys: pytest.CaptureFixture[str]) -> None:
    # tmp_cwd is empty — no pyproject.toml; CLI/env not set
    assert project_name() == "pytest-tc"
    captured = capsys.readouterr()
    assert "fallback project name" in captured.err
    # one-shot: second call does not warn
    capsys.readouterr()
    assert project_name() == "pytest-tc"
    captured = capsys.readouterr()
    assert captured.err == ""


def test_project_name_fallback_warning_suppressed_by_quiet(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_QUIET", "1")
    assert project_name() == "pytest-tc"
    captured = capsys.readouterr()
    assert captured.err == ""


def test_reuse_enabled_cli_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")
    reuse.set_cli_reuse(False)
    assert is_reuse_enabled() is False


def test_reuse_enabled_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "true")
    assert is_reuse_enabled() is True


def test_reuse_disabled_by_default() -> None:
    assert is_reuse_enabled() is False


def test_plugin_disabled_via_cli() -> None:
    reuse.set_cli_disabled(True)
    assert is_plugin_disabled() is True


def test_plugin_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS", "0")
    assert is_plugin_disabled() is True


def test_plugin_enabled_by_default() -> None:
    assert is_plugin_disabled() is False


def test_reuse_name_for_uses_override(tmp_cwd) -> None:
    name = reuse_name_for("psql", override="MY_NAME")
    assert name == "my_name"


def test_reuse_name_for_composes(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "MyProj"
        """)
    )
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert reuse_name_for("psql") == "myproj-tc-psql-master"


def test_reuse_name_for_with_xdist_worker(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "MyProj"
        """)
    )
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    assert reuse_name_for("redis") == "myproj-tc-redis-gw7"
