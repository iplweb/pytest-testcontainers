"""Local test matrix across supported pytest majors.

Mirrors the CI ``unit-tests`` job: one ephemeral environment per pytest
major, running the Docker-free unit suite. Uses uv as the venv backend to
stay consistent with the rest of the project (no plain pip).

    uv run nox                      # run every pytest major
    uv run nox -s tests             # same (the only session)
    uv run nox -- -k some_test      # forward extra args to pytest
    uv run nox -s "tests(pytest_major='9')"   # just one major

Docker-requiring tests are excluded here on purpose; CI runs those in a
separate, non-matrixed integration job. Run them locally with a plain
``uv run pytest tests/`` against your default environment.
"""

import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

# Keep in lockstep with the `pytest` matrix in .github/workflows/ci.yml
# and the supported-versions tables in README.md / CLAUDE.md.
PYTEST_MAJORS = ["7", "8", "9"]


@nox.session(python="3.13")
@nox.parametrize("pytest_major", PYTEST_MAJORS)
def tests(session: nox.Session, pytest_major: str) -> None:
    """Run the unit suite (no Docker) against one pytest major."""
    session.install("-e", ".[dev]")
    # `~=X.0` is the compatible-release operator: >=X.0,<(X+1).0
    session.install(f"pytest~={pytest_major}.0")
    session.run(
        "pytest",
        "tests/",
        "-m",
        "not docker_required",
        "-v",
        *session.posargs,
    )
