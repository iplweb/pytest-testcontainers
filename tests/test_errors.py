"""Sanity for the exception classes and the HybridFixtureError shape."""

from __future__ import annotations

from pytest_testcontainers.errors import (
    ContainerStartError,
    DockerNotRunningError,
    HybridFixtureError,
    PytestTestcontainersError,
    ReuseConflictError,
)


def test_hierarchy() -> None:
    assert issubclass(DockerNotRunningError, PytestTestcontainersError)
    assert issubclass(ContainerStartError, PytestTestcontainersError)
    assert issubclass(ReuseConflictError, PytestTestcontainersError)
    assert issubclass(HybridFixtureError, PytestTestcontainersError)
    assert issubclass(PytestTestcontainersError, RuntimeError)


def test_hybrid_fixture_error_carries_fields() -> None:
    exc = HybridFixtureError(
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


def test_hybrid_fixture_error_with_no_exit_code_shows_no_field() -> None:
    exc = HybridFixtureError(
        "client failed",
        command='CREATE DATABASE "x"',
        stderr="OperationalError(...)",
        exit_code=None,
    )
    rendered = str(exc)
    assert "exit_code=" not in rendered
