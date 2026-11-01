# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.3] - 2026-11-01

### Changed

- Import container classes from `testcontainers.community.*`. The top-level
  `testcontainers.postgres`, `.redis`, `.mysql`, `.mongodb` and `.rabbitmq`
  modules are deprecated shims since testcontainers 4.15.0, which is now the
  minimum supported version.

## [0.3.2] - 2026-08-07

No runtime change — the package content is identical to 0.3.1. This release
exists to move publishing onto a verifiable path and to prove that path works.

### Changed

- Releases are now published from GitHub Actions via PyPI Trusted Publishing
  (OIDC) on a `v*` tag, instead of `uv publish` from a maintainer's machine
  against a long-lived API token. Nothing needs to hold an upload credential
  any more. The workflow refuses to publish unless lint and the non-Docker
  suite pass, the tag matches the version in `pyproject.toml` (PyPI never lets
  a version be re-uploaded, so a mismatch is unfixable), and
  `twine check --strict` is clean.
- GitHub Actions are pinned to their Node 24 releases — `actions/checkout`
  v7.0.1 and `astral-sh/setup-uv` v9.0.0. GitHub had deprecated Node 20 and
  was already force-running the old pins on Node 24.

## [0.3.1] - 2026-08-07

### Fixed

- **A container killed while it was still booting was left behind.** Teardown
  was armed after `.start()` returned, but `.start()` blocks until the wait
  strategy passes — many seconds for a Postgres replaying an init script, and
  precisely the window in which someone watching a stuck machine reaches for
  `kill`. The container existed for that whole time with nothing registered to
  remove it, so the run died and left it to Ryuk. Teardown is now armed before
  the container is started; `stop()` is a no-op while the wrapper has no
  container yet. This also covers a start that fails its own wait strategy,
  which likewise leaves a container behind.

## [0.3.0] - 2026-08-07

### Added

- **Deterministic teardown on `SIGTERM` / `SIGHUP`.** Cleanup previously ran
  from `pytest_unconfigure` and `atexit` only, neither of which fires on these
  signals — killing a wedged run left its containers alive until Ryuk noticed
  the dropped socket ~10 s later, or forever if the Docker daemon restarted
  first. A handler is now installed for as long as containers are live; it
  stops them, restores the previous disposition and re-raises, so exit status
  semantics are unchanged. Skipped off the main thread, and skipped for any
  signal already set to `SIG_IGN` so `nohup pytest …` still survives a hangup.
  `SIGKILL` cannot be intercepted and still falls through to Ryuk.
- `--testcontainers-clean-project`, the previous narrow behaviour of
  `--testcontainers-clean` (name prefix only). Use it on a host where another
  pytest run may be in flight.

### Changed

- **The Ryuk reaper is shut down at the end of a session.**
  testcontainers-python starts one per process — so one per xdist worker, each
  privileged with an rw bind-mount of the Docker socket — and never closes it;
  it idled roughly 60 s past the end of every run. Skipped entirely for
  sessions that never started a container, so they do not pay for importing
  testcontainers to discover there is nothing to do.
- **`--testcontainers-clean` now also selects on the `org.testcontainers=true`
  label and on `testcontainers-ryuk-*`**, not just the `<project>-tc-` name
  prefix, so it reaches containers started without an explicit name and stray
  reapers. On a shared host this also removes containers belonging to a
  concurrent run — see the README, or use `--testcontainers-clean-project`.

### Fixed

- Test suite no longer fails against testcontainers >= 4.15, which deprecated
  `testcontainers.<service>` in favour of `testcontainers.community.<service>`.
  The warning is raised with `stacklevel=2`, so Python attributes it to the
  importing module and the existing module-keyed ignore could not match it.
  The imports themselves are unchanged — the old paths are the only ones that
  exist across the whole supported range (`testcontainers >=4.7,<5`).

## [0.2.2] - 2026-06-29

### Fixed

- Restored the `reuse_name_for` name in `pytest_testcontainers.reuse` as a
  backward-compatible alias of `container_name_for`. The template-based
  container-naming feature renamed the function, and 0.2.1 shipped that rename —
  which broke `pytest-testcontainers-django` < 0.2.5 (it imports the old name)
  at import time. The alias keeps existing installs working; the default
  `reuse_mode=True` matches the old function's byte-stable behavior.

## [0.2.1] - 2026-06-29

### Fixed

- **`Docker daemon is not reachable` even though `docker ps` worked**, on setups
  where the daemon lives on a non-default socket (OrbStack, colima, a stopped
  Docker Desktop). `docker.from_env()` — used by docker-py, by
  testcontainers' own client, and by Ryuk's socket bind-mount — honors
  `DOCKER_HOST` but, unlike the `docker` CLI, ignores the active
  `docker context`, so it fell back to `/var/run/docker.sock` (absent or a
  dangling symlink) and crashed with `FileNotFoundError`. The plugin now
  exports `DOCKER_HOST` from the active context before any client is built, so
  the whole process resolves Docker the way the CLI does. An explicit
  `DOCKER_HOST` still wins; with no resolvable context it falls back to the
  platform default socket.

## [0.2.0] - 2026-06-18

### Added

- Support for pytest 9.x. The CI `unit-tests` job now runs a 4 Python × 3
  pytest (7/8/9) matrix, and a new `noxfile.py` runs the same pytest-version
  matrix locally (`uv run nox`).

### Changed

- Widened the `pytest` dependency cap from `<9` to `<10`.

## [0.1.0] - 2026-05-08

Initial release.

### Added

- Maker functions for Postgres, Redis, MySQL, MongoDB, RabbitMQ and a generic
  `make_container()` escape hatch — each is a context manager wrapping the
  upstream `testcontainers-python` class.
- Session-scoped fixtures: `tc_psql`, `tc_redis`, `tc_mysql`, `tc_mongo`,
  `tc_rabbitmq`.
- Function-scoped fixtures: `tc_psql_func`, `tc_redis_func`, `tc_mysql_func`,
  `tc_mongo_func`, `tc_rabbitmq_func`.
- Clean-session fixtures (session container + per-test fresh state):
  `tc_psql_db`, `tc_mysql_db`, `tc_mongo_db`, `tc_redis_clean`.
- Clean-session admin commands run via Python client
  (`psycopg`/`pymysql`/`pymongo`) when importable; `docker exec` fallback
  otherwise. Redis clean-session is exec-only.
- Reuse mode (`--testcontainers-reuse` / `PYTEST_TESTCONTAINERS_REUSE=1`):
  named per-worker containers (`<project>-tc-<service>-<worker_id>`), Ryuk
  disabled, port-conflict detection on stopped-container restart.
- `--testcontainers-clean` to remove all `<project>-tc-*` containers.
- Normalized `pytest.UsageError` when Docker daemon is unreachable.
- `atexit` safety net for cleanup when `pytest_unconfigure` is skipped.
- Optional dependency extras: `clients-postgres`, `clients-mysql`,
  `clients-mongo`, aggregate `clients`.
