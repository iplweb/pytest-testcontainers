"""Reuse-name resolution: project-name precedence, sanitization, composition."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from pytest_testcontainers import reuse
from pytest_testcontainers.errors import NameTemplateError
from pytest_testcontainers.reuse import (
    DEFAULT_NAME_TEMPLATE,
    container_name_for,
    finalize_name,
    is_plugin_disabled,
    is_reuse_enabled,
    name_template,
    project_name,
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


def test_project_name_PROJECT_NAME_env(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("PROJECT_NAME", "from-project-name")
    assert project_name() == "from-project-name"


def test_project_name_COMPOSE_PROJECT_NAME_env(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "from-compose")
    assert project_name() == "from-compose"


def test_project_name_PROJECT_NAME_beats_compose(monkeypatch: pytest.MonkeyPatch, tmp_cwd) -> None:
    monkeypatch.setenv("PROJECT_NAME", "wins")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "loses")
    assert project_name() == "wins"


def test_project_name_plugin_env_beats_PROJECT_NAME(
    monkeypatch: pytest.MonkeyPatch, tmp_cwd
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "plugin-env")
    monkeypatch.setenv("PROJECT_NAME", "generic-env")
    assert project_name() == "plugin-env"


def test_project_name_PROJECT_NAME_beats_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_cwd
) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "from-pyproject"
        """)
    )
    monkeypatch.setenv("PROJECT_NAME", "from-project-name")
    assert project_name() == "from-project-name"


def test_project_name_cwd_basename_fallback(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No pyproject, no env: fall back to the cwd directory basename, no warning.
    app = tmp_cwd / "myapp"
    app.mkdir()
    monkeypatch.chdir(app)
    assert project_name() == "myapp"
    assert capsys.readouterr().err == ""


def test_project_name_literal_fallback_emits_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Filesystem root has an empty basename → literal fallback + one-shot warning.
    assert reuse._project_name_from_dir(Path("/")) == "pytest-tc"
    assert "fallback project name" in capsys.readouterr().err
    # one-shot: second call is silent
    assert reuse._project_name_from_dir(Path("/")) == "pytest-tc"
    assert capsys.readouterr().err == ""


def test_project_name_literal_fallback_suppressed_by_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_QUIET", "1")
    assert reuse._project_name_from_dir(Path("/")) == "pytest-tc"
    assert capsys.readouterr().err == ""


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


# --- template rendering -------------------------------------------------


def test_render_expands_all_placeholders() -> None:
    values = {
        "project": "myproj",
        "service": "psql",
        "branch": "feature-x",
        "dir": "checkout",
        "dirtag": "3f9ac1b2",
        "worker": "gw0",
        "rand": "ab12",
    }
    out = reuse._render_template(
        "{project}-tc-{service}-{branch}-{dir}-{dirtag}-{worker}-{rand}", values
    )
    assert out == "myproj-tc-psql-feature-x-checkout-3f9ac1b2-gw0-ab12"


def test_render_unknown_placeholder_raises() -> None:
    with pytest.raises(NameTemplateError) as exc:
        reuse._render_template("{project}-{nope}", {"project": "p"})
    assert "nope" in str(exc.value)
    assert "project" in str(exc.value)  # message lists valid placeholders


def test_render_rejects_miscased_placeholder() -> None:
    with pytest.raises(NameTemplateError) as exc:
        reuse._render_template("{Project}-{service}", {"project": "p", "service": "s"})
    assert "Project" in str(exc.value)


def test_render_rejects_placeholder_with_digit() -> None:
    with pytest.raises(NameTemplateError):
        reuse._render_template("{dir2}", {"project": "p"})


def test_finalize_collapses_empty_branch() -> None:
    # An empty {branch} leaves a double dash that finalize collapses cleanly.
    rendered = reuse._render_template(
        DEFAULT_NAME_TEMPLATE,
        {
            "project": "myproj",
            "service": "psql",
            "branch": "",
            "dir": "d",
            "dirtag": "3f9ac1b2",
            "worker": "master",
            "rand": "",
        },
    )
    assert finalize_name(rendered) == "myproj-tc-psql-3f9ac1b2-master"


# --- finalize_name length safety ---------------------------------------


def test_finalize_name_short_passthrough() -> None:
    assert finalize_name("MyProj-TC-Psql-Master") == "myproj-tc-psql-master"


def test_finalize_name_caps_at_255() -> None:
    out = finalize_name("x" * 400)
    assert len(out) <= 255


def test_finalize_name_deterministic() -> None:
    long = "y" * 400
    assert finalize_name(long) == finalize_name(long)


def test_finalize_name_distinguishes_tail() -> None:
    base = "z" * 300
    a = finalize_name(base + "-alpha")
    b = finalize_name(base + "-omega")
    assert a != b  # hash over the full name, not just the kept prefix


# --- name_template precedence ------------------------------------------


def test_name_template_default(tmp_cwd) -> None:
    assert name_template() == DEFAULT_NAME_TEMPLATE


def test_name_template_env_over_pyproject(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [tool.pytest_testcontainers]
            name_template = "{service}-from-pyproject"
        """)
    )
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{service}-from-env")
    assert name_template() == "{service}-from-env"


def test_name_template_pyproject(tmp_cwd) -> None:
    (tmp_cwd / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [tool.pytest_testcontainers]
            name_template = "{service}-from-pyproject"
        """)
    )
    assert name_template() == "{service}-from-pyproject"


def test_name_template_cli_wins(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{service}-env")
    reuse.set_cli_name_template("{service}-cli")
    assert name_template() == "{service}-cli"


# --- container_name_for end to end -------------------------------------


def test_container_name_for_override(tmp_cwd) -> None:
    assert container_name_for("psql", override="MY_NAME") == "my_name"


def test_container_name_for_reuse_is_byte_stable(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{project}-tc-{service}-{worker}")
    monkeypatch.setenv("PROJECT_NAME", "myproj")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    first = container_name_for("psql", reuse_mode=True)
    second = container_name_for("psql", reuse_mode=True)
    assert first == second == "myproj-tc-psql-master"


def test_container_name_for_default_appends_rand(tmp_cwd, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{project}-tc-{service}-{worker}")
    monkeypatch.setenv("PROJECT_NAME", "myproj")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    name = container_name_for("psql", reuse_mode=False)
    assert re.match(r"^myproj-tc-psql-master-[0-9a-f]{4}$", name)


def test_container_name_for_explicit_rand_not_double_appended(
    tmp_cwd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{service}-{rand}")
    name = container_name_for("psql", reuse_mode=False)
    assert re.match(r"^psql-[0-9a-f]{4}$", name)  # exactly one rand token


def test_reuse_name_for_backward_compat_alias(tmp_cwd) -> None:
    """``reuse_name_for`` was renamed to ``container_name_for`` in 0.2.1.

    Keep a backward-compatible alias so pytest-testcontainers-django < 0.2.5
    (which imports the old name) doesn't break on the rename.
    """
    from pytest_testcontainers.reuse import reuse_name_for

    assert reuse_name_for is container_name_for
    # Old name's callers expect the byte-stable reuse name (reuse_mode=True).
    assert reuse_name_for("psql") == container_name_for("psql", reuse_mode=True)
