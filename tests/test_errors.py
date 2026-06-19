"""Sanity for the exception classes and the CleanSessionFixtureError shape."""

from __future__ import annotations

import pytest

from pytest_testcontainers.errors import (
    CleanSessionFixtureError,
    ContainerStartError,
    DockerNotRunningError,
    PytestTestcontainersError,
    ReuseConflictError,
)


def test_hierarchy() -> None:
    assert issubclass(DockerNotRunningError, PytestTestcontainersError)
    assert issubclass(ContainerStartError, PytestTestcontainersError)
    assert issubclass(ReuseConflictError, PytestTestcontainersError)
    assert issubclass(CleanSessionFixtureError, PytestTestcontainersError)
    assert issubclass(PytestTestcontainersError, RuntimeError)


def test_clean_session_fixture_error_carries_fields() -> None:
    exc = CleanSessionFixtureError(
        "boom",
        command="CREATE DATABASE 'x'",
        stderr="connection refused",
        exit_code=42,
    )
    assert exc.command == "CREATE DATABASE 'x'"
    assert exc.stderr == "connection refused"
    assert exc.exit_code == 42
    rendered = str(exc)
    assert "boom" in rendered
    assert "exit_code=42" in rendered
    assert "connection refused" in rendered


def test_clean_session_fixture_error_with_no_exit_code_shows_no_field() -> None:
    exc = CleanSessionFixtureError(
        "client failed",
        command='CREATE DATABASE "x"',
        stderr="OperationalError(...)",
        exit_code=None,
    )
    rendered = str(exc)
    assert "exit_code=" not in rendered


def test_name_template_error_is_plugin_error() -> None:
    from pytest_testcontainers import NameTemplateError
    from pytest_testcontainers.errors import NameTemplateError as ErrNameTemplateError
    from pytest_testcontainers.errors import PytestTestcontainersError

    assert NameTemplateError is ErrNameTemplateError
    assert issubclass(NameTemplateError, PytestTestcontainersError)
    with pytest.raises(PytestTestcontainersError):
        raise NameTemplateError("bad placeholder")
