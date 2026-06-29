# pytest-testcontainers

[![CI](https://github.com/iplweb/pytest-testcontainers/actions/workflows/ci.yml/badge.svg)](https://github.com/iplweb/pytest-testcontainers/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pytest-testcontainers.svg)](https://pypi.org/project/pytest-testcontainers/)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-testcontainers.svg)](https://pypi.org/project/pytest-testcontainers/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Named pytest fixtures + a maker convention on top of
[testcontainers-python](https://testcontainers-python.readthedocs.io/),
plus clean-session "fresh DB per test" fixtures that get you per-test isolation
without per-test container start.**

`testcontainers-python` is a great library, but it ships **no pytest
fixtures** and **no pytest entry point**. Every project that wants
`def test_db(pg): …` ends up rewriting the same ~150 lines of
`conftest.py`: session-scoped fixtures times five services, times
correct xdist worker handling, times reuse mode with stable names and
Ryuk disabled, times a normalized "Docker is not running" error.

`pytest-testcontainers` ships those 150 lines.

## Why you might want this

- **Trivial defaults**: `def test_x(tc_psql): …` boots `postgres:16`
  once per worker, gives you back a `PostgresContainer`, and tears it
  down at session end.
- **Custom images in one line**: write your own fixture against
  `make_postgres(image="acme/pg-with-extensions:16")` — the rest of
  your test code is unchanged because the maker returns the same
  upstream class your test already knows.
- **Clean-session "fresh DB per test"**: `tc_psql_db` creates a
  `test_<hex>` database before the test and drops it after — same
  isolation as a per-test container, **~480× faster**.
- **Reuse mode for fast dev loops**: `--testcontainers-reuse` keeps
  named containers alive between pytest runs (Ryuk disabled, stable
  per-worker names) so iterative dev doesn't pay the container-start
  tax every iteration.
- **Normalized errors**: "Docker daemon not reachable?" gets you a
  human-readable `pytest.UsageError` with remediation, not a wall of
  docker-py traceback.
- **No magic**: just `@pytest.fixture` decorators on top of the
  upstream classes. No env-var injection, no conftest re-import dance,
  no `pytest_load_initial_conftests` cleverness. Read the source in 15
  minutes.

## Installation

### Using uv (recommended)

```bash
uv add --group dev pytest-testcontainers
```

### Using pip

```bash
pip install pytest-testcontainers
```

For the clean-session fast path (auto-detects clients; no install needed
for basic functionality):

```bash
# all clean-session fast paths in one shot
pip install pytest-testcontainers[clients]

# or pick the ones you need
pip install pytest-testcontainers[clients-postgres]
pip install pytest-testcontainers[clients-mysql]
pip install pytest-testcontainers[clients-mongo]
```

You also need a working Docker daemon — Docker Desktop, colima, Rancher
Desktop, or Podman with the Docker socket all work. No platform-specific
extra steps; if `docker.from_env().ping()` works, this plugin works.

## Quickstart

```python
def test_my_thing(tc_psql):
    host = tc_psql.get_container_host_ip()
    port = int(tc_psql.get_exposed_port(5432))
    # connect with your library of choice (psycopg, SQLAlchemy, asyncpg, ...)
```

That's the whole "hello world." `tc_psql` is the upstream
`PostgresContainer` — every method/attribute on it works the same as
when you build it yourself.

## Services × variants

Each service ships three fixture variants plus a maker function:

| Service  | Maker            | Session fixture  | Function-scoped fixture     | Clean-session fixture    |
|----------|------------------|------------------|-----------------------|-------------------|
| Postgres | `make_postgres`  | `tc_psql`        | `tc_psql_func`       | `tc_psql_db`      |
| Redis    | `make_redis`     | `tc_redis`       | `tc_redis_func`      | `tc_redis_clean`  |
| MySQL    | `make_mysql`     | `tc_mysql`       | `tc_mysql_func`      | `tc_mysql_db`     |
| MongoDB  | `make_mongo`     | `tc_mongo`       | `tc_mongo_func`      | `tc_mongo_db`     |
| RabbitMQ | `make_rabbitmq`  | `tc_rabbitmq`    | `tc_rabbitmq_func`   | —                 |
| (any)    | `make_container(Cls, **kw)` | — | — | — |

- **Session** = one container per worker, lazy first-request start,
  shared across all tests. The right default.
- **Function-scoped** = fresh container per test. **Almost always wrong**;
  see the scope ladder below.
- **Clean-session** = session container + per-test fresh DB / keyspace.
  What most users who think they need a function-scoped fixture actually
  want.
- **`make_container(Cls, ...)`** is the escape hatch for services we
  don't ship a fixture for (Kafka, Localstack, your bespoke image).

## Image overrides — write a custom fixture in one line

You don't override defaults in some config file — you write your own
fixture using the maker:

```python
import pytest
from pytest_testcontainers import make_postgres

# Postgres 18.
@pytest.fixture(scope="session")
def pg_18():
    with make_postgres(image="postgres:18") as pg:
        yield pg

# Specific minor version (pinning for reproducibility).
@pytest.fixture(scope="session")
def pg_pinned():
    with make_postgres(image="postgres:16.13") as pg:
        yield pg

# Custom user-built image (e.g. one with extensions baked in).
@pytest.fixture(scope="session")
def my_psql():
    with make_postgres(image="acme/pg-with-postgis:16") as pg:
        yield pg

# Custom image + extra env (image-specific tuning knobs).
@pytest.fixture(scope="session")
def my_psql_fast():
    with make_postgres(
        image="acme/pg-with-postgis:16",
        env={
            "POSTGRES_INITDB_ARGS": "--encoding=UTF8",
            "POSTGRES_HOST_AUTH_METHOD": "trust",
        },
    ) as pg:
        yield pg

# Custom credentials (must match what the app expects).
@pytest.fixture(scope="session")
def my_psql_creds():
    with make_postgres(
        image="acme/pg:16",
        username="appuser",
        password="appsecret",
        database="appdb",
    ) as pg:
        yield pg
```

Same pattern for `make_redis`, `make_mysql`, `make_mongo`,
`make_rabbitmq`. **Swapping the image is a one-line change**; the rest
of your test code is unchanged because the maker returns a vanilla
`testcontainers-python` instance with the same API.

### Override the built-in `tc_psql` directly

If you want *one* customized PG everywhere instead of writing a new
fixture name, redefine `tc_psql` in your own `conftest.py` — pytest's
nearest-conftest resolution takes the user's version and silently
shadows the plugin's:

```python
# conftest.py
import pytest
from pytest_testcontainers import make_postgres

@pytest.fixture(scope="session")
def tc_psql():
    with make_postgres(image="acme/pg:16", username="bpp", password="pw", database="bpp") as pg:
        yield pg
```

## Container scope: when to use what

This is the educational core. **Read this before reaching for
`tc_psql_func`.**

### Default: session fixture (`tc_psql`)

One container per worker, shared across all tests. Cheapest, fastest.
The right default.

Use for:
- Read-heavy tests.
- Tests that wrap their work in transactions and roll back
  (pytest-django's `db` fixture; SQLAlchemy savepoints).
- Tests that delete their own rows in teardown.

Don't use for:
- DDL-heavy tests (`CREATE TABLE` / `ALTER TABLE` / `CREATE EXTENSION`)
  unless rolled back inside a transaction.
- Tests that mutate global server state (`ALTER SYSTEM SET …`,
  replication slots, prepared statements that persist).

### Clean-session: session container + fresh DB (`tc_psql_db`)

Same container as above, but each test gets a brand-new database
created and dropped around it.

Use for:
- DDL-heavy suites (Django migration tests; schema-change tests).
- Tests that vacuum, reset sequences with autocommit, or do anything
  else that fights with transactional isolation.
- Replication slot / `pg_stat_*` tests where you want a clean view.

Cost: ~50–200ms per test for `CREATE DATABASE` / `DROP DATABASE`. Two
orders of magnitude faster than per-test container start.

This is what most users who think they need a function-scoped container
actually want.

### Function-scoped container (`tc_psql_func`) — **avoid**

Fresh container per test. Available, **strongly discouraged**.

The math, with concrete numbers:

> **Container start = 3–5 seconds for Postgres.**
> **100 tests × 5s = 8 minutes wasted on container startup.**
> **The clean-session pattern gives the same isolation ~480× faster**
> (50ms per fresh DB vs 5s per fresh container).

Use ONLY when:

1. Test mutates global server config that can't be reset (`ALTER
   SYSTEM SET …` followed by a restart requirement).
2. Test corrupts container-level state (broken cluster, FS corruption,
   killed `postmaster`, etc.).
3. You're testing the testcontainer machinery itself.

If your reasoning is "I want isolation between tests," that's the
clean-session pattern, not this.

### Why we don't ship "shared DB + transaction rollback"

(Sometimes called "Variant B".) **Doesn't work for**:
- DDL tests (migrations, schema changes) — DDL implicit-commits in
  many SQL dialects, breaking the rollback-around-each-test model.
- `VACUUM`, sequence operations with autocommit.
- Replication state.
- Anything cross-connection.

If you genuinely want this pattern for your read-heavy suite, write
your own fixture against `tc_psql` — but this plugin doesn't try to
ship a one-size-fits-all transactional layer.

## Surprising thing #1: xdist `scope="session"` is per-worker

If you only read one section, read this one.

Under `pytest-xdist`, each worker process runs its own pytest session.
`@pytest.fixture(scope="session")` therefore means **"one per
worker"**, NOT "one for the whole pytest invocation". With `-n 8`,
you get **8 containers**, one per worker:

```
$ pytest -n 4 tests/                   # 4 workers
[gw0] container started: postgres on localhost:54321
[gw1] container started: postgres on localhost:54322
[gw2] container started: postgres on localhost:54323
[gw3] container started: postgres on localhost:54324
```

Many users assume the opposite (because "session" sounds like
"global"). It isn't.

If you need **one container shared across all workers**, you need to
start it before pytest forks workers — that's eager-start machinery
that lives in
[`pytest-testcontainers-django`](https://pypi.org/project/pytest-testcontainers-django/)
(if you're a Django user) or in a wrapper script:

```bash
# wrapper.sh — start once, pass coordinates to all workers
docker run -d --rm --name shared-pg \
    -e POSTGRES_PASSWORD=test \
    -p 54321:5432 postgres:16
export SHARED_PG_HOST=localhost
export SHARED_PG_PORT=54321
trap "docker rm -f shared-pg" EXIT
PYTEST_TESTCONTAINERS=0 pytest -n 8 tests/
```

```python
# conftest.py — each worker reads env, no container started by us
import os, pytest

@pytest.fixture(scope="session")
def shared_pg():
    return {
        "host": os.environ["SHARED_PG_HOST"],
        "port": int(os.environ["SHARED_PG_PORT"]),
        "username": "postgres",
        "password": "test",
    }
```

`PYTEST_TESTCONTAINERS=0` (or `--no-testcontainers`) disables our
`tc_*` fixtures so they don't try to start their own per-worker
container alongside yours.

## Clean-session pattern walkthrough — `tc_psql_db`

`tc_psql_db` yields a `DbConnInfo` (host/port/user/pass/db) for a
brand-new Postgres database created on the session-scoped `tc_psql`
container. The DB is dropped after the test:

```python
from pytest_testcontainers import DbConnInfo

def test_isolated_schema(tc_psql_db: DbConnInfo):
    # tc_psql_db.database is e.g. "test_a3f9b2c1d4e5"
    url = tc_psql_db.url()  # "postgresql://test:test@localhost:5xxxx/test_a3f9b2c1d4e5"

    import psycopg
    with psycopg.connect(**dataclasses.asdict(tc_psql_db)) as conn:
        conn.execute("CREATE TABLE widgets (id serial PRIMARY KEY)")
        # …
    # On teardown, DROP DATABASE … WITH (FORCE) wipes everything.
```

The DB name is `test_<12-hex>` (`secrets.token_hex(6)` — ~6e13 unique
values per session, more than enough for any conceivable test count).
Same shape for `tc_mysql_db`, `tc_mongo_db`. `tc_redis_clean`
`FLUSHALL`-s the session Redis container after each test instead.

### How admin commands run

We need to issue six single-shot commands behind the clean-session fixtures
(`CREATE DATABASE`, `DROP DATABASE`, `dropDatabase()`, `FLUSHALL`).
Two paths:

1. **Python client fast path** (~5ms/call) — used automatically when
   `psycopg` / `pymysql` / `pymongo` is importable.
2. **`docker exec` fallback** (~50–100ms/call) — used otherwise. Zero
   host-side client deps. Works out of the box.

Both raise the same `CleanSessionFixtureError` so user code catches one
exception type. To get the fast path everywhere in one install:

```bash
pip install pytest-testcontainers[clients]
```

Or per-service: `pytest-testcontainers[clients-postgres]`,
`[clients-mysql]`, `[clients-mongo]`. Redis is exec-only by design — for
a single `FLUSHALL`, opening a TCP connection costs more than just
exec-ing the in-container CLI.

The fallback emits a one-shot stderr advisory the first time it runs
in a session; suppress with `PYTEST_TESTCONTAINERS_QUIET=1`.

## Building custom services with `make_container`

For services we don't ship a maker for, use the generic escape hatch:

```python
import pytest
from testcontainers.kafka import KafkaContainer
from pytest_testcontainers import make_container

@pytest.fixture(scope="session")
def tc_kafka():
    with make_container(KafkaContainer, image="confluentinc/cp-kafka:7.5.0") as k:
        yield k
```

Every plumbing concern — daemon ping, reuse name, atexit cleanup,
Ryuk-disable-when-reuse — applies. `args`/`kwargs` go to the upstream
constructor verbatim.

## Container names

Every container the plugin starts is named so you can find it in `docker ps`
(previously default-mode containers got random Docker names like
`heuristic_cannon`). The name is built from a template:

```
{project}-tc-{service}-{branch}-{dirtag}-{worker}
```

e.g. `myproj-tc-psql-feature-x-3f9ac1b2-master`.

Placeholders:

| Placeholder | Meaning |
|-------------|---------|
| `{project}` | Project name (see precedence below). |
| `{service}` | Service slug (`psql`, `redis`, `mysql`, `mongo`, `rabbitmq`, or derived from the container class). |
| `{branch}`  | Current git branch (read from `.git/HEAD`, no subprocess). Detached HEAD → 12-char commit SHA. No repo → empty. |
| `{dir}`     | Worktree-root basename. Readable, not collision-proof. |
| `{dirtag}`  | 8-hex hash of the worktree-root absolute path. Disambiguates the same branch checked out in two directories. |
| `{worker}`  | xdist worker id (`master` when not under xdist). |
| `{rand}`    | 4 random hex chars. Empty in reuse mode; auto-appended in default mode when the template omits it. |

Empty placeholders collapse cleanly (no `--`, no dangling dashes). Names are
sanitized to Docker's charset and capped at 255 chars (trim + checksum).

Override the template (CLI > env > pyproject > built-in default):

```bash
pytest --testcontainers-name-template='{project}-{service}-{branch}'
# or
export PYTEST_TESTCONTAINERS_NAME_TEMPLATE='{project}-{service}-{branch}'
```

```toml
# pyproject.toml
[tool.pytest_testcontainers]
name_template = "{project}-{service}-{branch}"
```

The `{project}` component resolves as: `--testcontainers-project` →
`PYTEST_TESTCONTAINERS_PROJECT` → `PROJECT_NAME` → `COMPOSE_PROJECT_NAME` →
`pyproject.toml [project].name` → cwd basename.

An unknown placeholder raises `NameTemplateError` listing the valid ones.

## Reuse mode

For iterative dev loops where you don't want to pay container-start
on every pytest run:

```bash
PYTEST_TESTCONTAINERS_REUSE=1 pytest tests/
# or
pytest --testcontainers-reuse tests/
```

What changes:
- Each container gets a stable, identifiable name from the template
  (default `{project}-tc-{service}-{branch}-{dirtag}-{worker}`), so reuse is
  scoped per project **and** per git branch **and** per directory. Switching
  branch or worktree yields a *new* container; the previous one lingers
  (stopped) until you run `pytest --testcontainers-clean`.
- Ryuk (testcontainers' reaper) is disabled so the named containers
  survive between runs.
- On the next run we look up by name — found-and-running gets bound
  immediately; found-stopped gets restarted; not-found falls through
  to fresh start.

To clean up:

```bash
pytest --testcontainers-clean
```

This stops and removes all `<project>-tc-*` containers and exits with
code 0 — a one-liner equivalent of the docker-py `containers.list +
remove(force=True)` loop.

**Two pytest invocations running concurrently**: each must use its
own `PYTEST_TESTCONTAINERS_PROJECT` to avoid name collisions. The
plugin doesn't auto-namespace by PID — that would defeat the "reuse
across runs" point.

> **Upgrading from 0.1.0:** reuse-mode names now encode branch + directory, so
> containers reused from an older version are not found by name — a fresh one
> is created and the old one lingers until `pytest --testcontainers-clean`.

## Configuration

No TOML config table. The handful of toggles read at maker-call time:

### Environment variables

| Variable                                  | Effect                                      |
|-------------------------------------------|---------------------------------------------|
| `PYTEST_TESTCONTAINERS=0`                 | Disable plugin fixtures (raise UsageError). |
| `PYTEST_TESTCONTAINERS_REUSE=1`           | Reuse named containers across runs.         |
| `PYTEST_TESTCONTAINERS_PROJECT=<name>`    | Override the `<project>` part of reuse names. |
| `PROJECT_NAME=<name>`                     | Generic project-name source (below the plugin var). |
| `COMPOSE_PROJECT_NAME=<name>`             | docker-compose's project var (below `PROJECT_NAME`). |
| `PYTEST_TESTCONTAINERS_NAME_TEMPLATE=<t>` | Override the container-name template.       |
| `PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK=1` | Skip Docker daemon ping (rare).             |
| `PYTEST_TESTCONTAINERS_QUIET=1`           | Suppress one-shot informational advisories. |

### CLI flags

| Flag                            | Same as                              |
|---------------------------------|--------------------------------------|
| `--no-testcontainers`           | `PYTEST_TESTCONTAINERS=0`            |
| `--testcontainers-reuse`        | `PYTEST_TESTCONTAINERS_REUSE=1`      |
| `--testcontainers-no-reuse`     | force fresh-each-run mode                 |
| `--testcontainers-project=NAME` | `PYTEST_TESTCONTAINERS_PROJECT=NAME` |
| `--testcontainers-name-template=T` | `PYTEST_TESTCONTAINERS_NAME_TEMPLATE=T` |
| `--testcontainers-clean`        | prune `<project>-tc-*` and exit 0    |

CLI > env > defaults.

## Error scenarios

When Docker is not running you get a normalized `pytest.UsageError`,
not a wall of docker-py traceback:

```
[pytest-testcontainers] Docker daemon is not reachable.
Is Docker Desktop / colima / Rancher Desktop running?

Options:
  - start it and re-run pytest, OR
  - disable the plugin: --no-testcontainers (or PYTEST_TESTCONTAINERS=0), OR
  - point at remote Docker via DOCKER_HOST.

Underlying error: ...
```

### `docker ps` works but pytest says the daemon is unreachable

You're almost certainly on a non-default **Docker context** — OrbStack,
colima, or a Docker Desktop install where `/var/run/docker.sock` is missing
or a dangling symlink. `docker.from_env()` (used by docker-py *and* by
testcontainers internally) honors `DOCKER_HOST` but, unlike the `docker`
CLI, ignores the active `docker context`. The plugin now resolves the active
context's endpoint for you and exports `DOCKER_HOST` before any container is
started, so it talks to the same daemon the CLI does.

If it still fails, point it at the daemon explicitly — an explicit
`DOCKER_HOST` always wins:

```bash
docker context inspect -f '{{.Endpoints.docker.Host}}'   # see the endpoint
export DOCKER_HOST=unix://$HOME/.orbstack/run/docker.sock # e.g. OrbStack
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock # e.g. colima
```

When a stopped reused container can't be brought back (typically
because the previously-mapped port is now held by something else):

```
[pytest-testcontainers] Cannot start existing container 'myproj-tc-psql-master': ...

Most common cause: another process now holds the port this
container was previously bound to. To start fresh:
    docker rm -f myproj-tc-psql-master
Or pass --testcontainers-no-reuse for this run.
```

When a clean-session admin command fails (`CREATE DATABASE` etc.), you get
`CleanSessionFixtureError` with `command`, `stderr`, and (for the
docker-exec path) `exit_code` — surfaced like any other in-test
exception.

## Comparison

| Tool                           | Backend         | Lifecycle              | Notes                                            |
|--------------------------------|-----------------|------------------------|--------------------------------------------------|
| `testcontainers-python`        | docker-py       | manual                 | The library this plugin sits on. No fixtures.    |
| **`pytest-testcontainers`**    | testcontainers  | session (lazy) / func  | **This.** Fixtures, makers, clean-session fixtures.     |
| `pytest-docker-compose`        | compose         | per-test (default)     | Different abstraction. Complementary, not a dup. |
| `pytest-docker`                | compose         | per-test               | Compose-driven; not testcontainers-driven.       |
| `pytest-container`             | testinfra       | image-under-test       | For testing image content, not dependency setup. |

If you need intra-network communication between several testcontainers,
a compose-based plugin is the better choice. Each service we start is
reachable on its mapped host port — that's enough for >95% of test
setups.

## Supported versions

### Python

| Python | 3.10 | 3.11 | 3.12 | 3.13 |
|--------|:----:|:----:|:----:|:----:|
|        | ✓    | ✓    | ✓    | ✓    |

CI runs the full test matrix on every supported version. 3.13 is the
default we develop against.

### pytest

| pytest | 7.x | 8.x | 9.x |
|--------|:---:|:---:|:---:|
|        | ✓   | ✓   | ✓   |

Tested separately in CI; floor is `pytest>=7.4`.

### Other runtime requirements

- testcontainers-python ≥ 4.7 (modern `wait_strategies` API)
- docker-py ≥ 6.1 (we use `docker.errors.NotFound`,
  `containers.exec_run(demux=True)`)
- A working Docker daemon: Docker Desktop / colima / Rancher Desktop /
  Podman with the Docker socket / remote Docker via `DOCKER_HOST`.

## License

MIT — see [LICENSE](LICENSE). Matches `testcontainers-python`'s
license.
