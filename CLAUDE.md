# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`pytest-testcontainers` is a pytest plugin (registered via the `pytest11` entry point `testcontainers = "pytest_testcontainers.plugin"`) that wraps [testcontainers-python](https://testcontainers-python.readthedocs.io/) with:

- Named fixtures (`tc_psql`, `tc_redis`, `tc_mysql`, `tc_mongo`, `tc_rabbitmq`) plus `_func` and `_db`/`_clean` variants.
- Maker functions (`make_postgres`, `make_redis`, ..., generic `make_container`) returning vanilla upstream containers so user code stays library-agnostic.
- A "clean-session" pattern (session container + fresh DB/keyspace per test) that gives per-test isolation ~480× faster than per-test container start.
- Reuse mode (`--testcontainers-reuse` / `PYTEST_TESTCONTAINERS_REUSE=1`) with stable per-worker names and Ryuk disabled.

The package layout enforces a clean public API: `src/pytest_testcontainers/__init__.py` is the only thing users import from. Everything under `_internal/` is private — do not re-export from there.

## Common commands

This project uses **uv**. Use it consistently — do not fall back to plain pip in this repo.

```bash
# Set up
uv venv --python 3.13
uv pip install -e ".[dev]"

# Lint + format check (same as CI)
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Auto-fix
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/

# Run the full suite
uv run pytest tests/ -v

# Unit-only (skips anything marked docker_required) — matches the CI "unit-tests" job
uv run pytest tests/ -m "not docker_required" -v

# Local pytest-version matrix (7.x / 8.x / 9.x) — mirrors the CI "unit-tests" job
uv run nox                                   # all majors
uv run nox -s "tests(pytest_major='9')"      # one major

# A single test
uv run pytest tests/test_makers_postgres.py::test_specific_thing -v

# xdist smoke (per-worker container behavior)
uv run pytest -n 2 tests/test_makers_postgres.py tests/test_makers_redis.py -v

# Reuse mode smoke (run twice; second run must reuse)
PYTEST_TESTCONTAINERS_REUSE=1 uv run pytest tests/test_reuse_mode.py -v
PYTEST_TESTCONTAINERS_REUSE=1 uv run pytest tests/test_reuse_mode.py -v

# Clean up reused containers
uv run pytest --testcontainers-clean
```

Tests use `filterwarnings = ["error", ...]` — any new warning fails the suite. Quiet the one-shot exec-fallback advisory in test runs with `PYTEST_TESTCONTAINERS_QUIET=1`.

`tests/conftest.py` auto-uses `_clean_state` to reset CLI overrides, the docker-ping cache, the fallback-warning gate, and all `PYTEST_TESTCONTAINERS*` env vars between tests. When adding module-level state in the plugin, wire its reset into `_clean_state` or tests will leak across each other.

Docker-requiring tests must be marked `@pytest.mark.docker_required` — the conftest auto-skips them when `docker.from_env().ping()` fails.

## Architecture

### Layering (strict — keep it this way)

```
src/pytest_testcontainers/
├── __init__.py             # public API surface (re-exports only)
├── plugin.py               # pytest hooks: addoption, configure, sessionstart, --testcontainers-clean
├── fixtures.py             # tc_psql / tc_redis / ... plus _func and _db/_clean variants
├── makers.py               # make_postgres / make_redis / ... / make_container (used by fixtures AND end users)
├── containers.py           # low-level start/wait/atexit plumbing shared by all makers
├── reuse.py                # reuse-mode logic: name resolution, find-or-create, project detection
├── errors.py               # exception hierarchy (PytestTestcontainersError + subclasses)
└── _internal/              # PRIVATE — not re-exported except DbConnInfo
    ├── conn_info.py        # DbConnInfo dataclass (the one _internal item that IS public)
    ├── docker_health.py    # cached daemon ping + normalized DockerNotRunningError
    ├── clean_session_admin.py  # CREATE/DROP DB via Python client OR docker exec fallback
    └── port_resolver.py    # host-port lookup helper
```

Direction of dependencies: `plugin` → `fixtures` → `makers` → `containers` → `_internal`. Don't introduce upward edges. `errors.py` is leaf — anything can import from it.

### Two-path admin commands (the non-obvious design)

The clean-session fixtures (`tc_psql_db`, `tc_mysql_db`, `tc_mongo_db`, `tc_redis_clean`) need to run six one-shot admin commands: `CREATE DATABASE`, `DROP DATABASE`, `dropDatabase()`, `FLUSHALL`. `clean_session_admin.py` resolves this two ways:

1. **Fast path** (~5ms): Python client (`psycopg`, `pymysql`, `pymongo`) when importable.
2. **Fallback** (~50–100ms): `docker exec` of the in-container CLI. Zero host-side client deps.

Both raise `CleanSessionFixtureError` so user code catches one type. The first fallback invocation per session emits a stderr advisory; `_reset_warnings_for_tests()` is the test hook that resets that gate. Redis is exec-only by design (one `FLUSHALL` isn't worth opening a TCP connection for).

### xdist mental model (the #1 user-facing surprise)

`@pytest.fixture(scope="session")` under `pytest-xdist` means **per worker**, not per invocation. With `-n 8` you get 8 containers. The plugin embraces this: per-worker reuse names use the worker id (`<project>-tc-<service>-<workerid>`, master when not under xdist). If you ever feel tempted to "fix" the per-worker behavior into a single shared container, don't — that's a different product (`pytest-testcontainers-django` does eager pre-fork start). The README has the wrapper-script pattern users should follow for one-shared-container.

### Reuse mode

`reuse.py` owns: project-name detection (reads `pyproject.toml` `[project].name`; falls back to cwd basename), name composition, find-or-create against docker-py (`docker.from_env().containers.get(name)`), and bridging found-stopped containers back into a `DockerContainer` instance. When reuse is on, the testcontainers Ryuk reaper is disabled so the named containers actually survive between runs. `reset_cli_state()` clears CLI-flag-derived module state and **must** be called between tests (the conftest does this).

### Errors

All plugin errors inherit `PytestTestcontainersError`. `DockerNotRunningError` is raised as `pytest.UsageError` at session start (human-readable remediation, not a docker-py traceback). `ReuseConflictError` carries the docker rm one-liner to recover. `CleanSessionFixtureError` carries `command`, `stderr`, and optional `exit_code` (for the docker-exec path). When adding a new failure mode, add a subclass — don't widen an existing one.

## Configuration precedence

CLI flag > env var > default. Implemented in `plugin.py` and read once into module state in `reuse.py`. The relevant knobs:

| Env var                                    | CLI flag                          |
|--------------------------------------------|-----------------------------------|
| `PYTEST_TESTCONTAINERS=0`                  | `--no-testcontainers`             |
| `PYTEST_TESTCONTAINERS_REUSE=1`            | `--testcontainers-reuse` / `--testcontainers-no-reuse` |
| `PYTEST_TESTCONTAINERS_PROJECT=NAME`       | `--testcontainers-project=NAME`   |
| `PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK=1`  | —                                 |
| `PYTEST_TESTCONTAINERS_QUIET=1`            | —                                 |
| —                                          | `--testcontainers-clean` (prune + exit 0) |

## Supported versions

Python 3.10–3.13. pytest 7, 8, and 9 (CI runs all three as a matrix; `noxfile.py` runs the same matrix locally). testcontainers ≥ 4.7 (modern `wait_strategies` API), docker-py ≥ 6.1 (needs `containers.exec_run(demux=True)` and `docker.errors.NotFound`). Don't add code paths that require newer-than-floor versions of these.

## Conventions

- Line length 100. Ruff selects `E,F,W,I,B,UP,SIM,RUF`; `E501` is ignored (formatter handles wrapping). Tests get `B011` exempted.
- Public symbols come out of `__init__.py`. Adding a new fixture/maker means: implement in `fixtures.py` / `makers.py`, re-export from `__init__.py`, document in `README.md`, add `SPEC.md` entry if behavior-defining.
- Don't import from `pytest_testcontainers._internal` in user-facing examples or docs — `DbConnInfo` is re-exported from the top-level package precisely so users never need to.
