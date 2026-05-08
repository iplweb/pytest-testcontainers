"""Plugin exception hierarchy.

User-facing daemon/reuse/start failures are converted to
``pytest.UsageError`` at maker boundaries (see ``makers.py``).
``HybridFixtureError`` is the only one preserved as-is for the user's
test report — it is a test-time failure, not a setup misconfiguration.
"""

from __future__ import annotations


class PytestTestcontainersError(RuntimeError):
    """Base exception. Plugin-internal; user code rarely sees this."""


class DockerNotRunningError(PytestTestcontainersError):
    """``docker.from_env().ping()`` failed."""


class ContainerStartError(PytestTestcontainersError):
    """A container failed ``.start()``. Wraps the testcontainers exc."""


class ReuseConflictError(PytestTestcontainersError):
    """An existing reused container exists but cannot be brought back to running.

    Typical cause: another process now holds the host port the container
    was previously bound to.
    """


class HybridFixtureError(PytestTestcontainersError):
    """A hybrid fixture failed to issue an admin command.

    Raised by ``tc_psql_db`` / ``tc_mysql_db`` / ``tc_mongo_db`` /
    ``tc_redis_clean`` from BOTH execution paths (Python client and
    ``docker exec``) so user code sees a single exception type.
    """

    def __init__(
        self,
        message: str,
        *,
        command: str,
        stderr: str,
        exit_code: int | None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.exit_code = exit_code

    def __str__(self) -> str:
        base = super().__str__()
        parts = [base, f"command={self.command!r}"]
        if self.exit_code is not None:
            parts.append(f"exit_code={self.exit_code}")
        if self.stderr:
            parts.append(f"stderr={self.stderr!r}")
        return " | ".join(parts)
