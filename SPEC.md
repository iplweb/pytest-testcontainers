# pytest-testcontainers — Specification

Status: DRAFT v0.2 (post-refactor; eager-start removed, fixture-only)
Audience: implementer + reviewer
Scope: package #1 of a three-package extraction from BPP
(`testcontainers_bpp` → `pytest-testcontainers` +
`pytest-testcontainers-django` + `django-pg-baseline`).

This document specifies ONLY the generic, framework-agnostic plugin.
The Django-specific eager-start / env-injection / settings-import
choreography lives exclusively in package #2. See §15 for boundaries.

---

## 1. Purpose

`pytest-testcontainers` is a thin pytest plugin that ships
**named, opinionated pytest fixtures** on top of `testcontainers-python`
for the most common services (Postgres, Redis, MySQL, MongoDB,
RabbitMQ), plus a **maker convention** (`make_postgres()`,
`make_redis()`, …) that returns context managers wrapping the upstream
classes — for users who need a custom image, custom credentials, or a
service we don't ship a fixture for.

### The gap

`testcontainers-python` is a **pure library**. It gives you
`PostgresContainer().start()` / `.get_exposed_port()` / `.stop()` and
nothing else. It does NOT ship pytest fixtures. It does NOT register a
pytest entry point. Every project that wants `def test_db(pg): …`
hand-rolls a `conftest.py` of the form:

```python
@pytest.fixture(scope="session")
def pg():
    with PostgresContainer("postgres:16") as p:
        yield p
```

…times five services, times correct xdist worker handling, times a
"function-scoped fresh DB" clean-session pattern, times reuse mode with stable
names and Ryuk disabled, times a normalized "Docker is not running"
error. Every project we have seen reinvents the same ~150 lines.

### Why the maker pattern (not just fixtures)

Fixtures alone would be too rigid: every project tweaks image,
credentials, shared_buffers, init scripts. Default fixtures cover the
80% case (`tc_psql` boots `postgres:16` with `test`/`test`/`test`); for
everything else, the user writes a one-liner against `make_postgres()`:

```python
@pytest.fixture(scope="session")
def my_pg():
    with make_postgres(image="iplweb/bpp-dbserver:psql-16.13",
                       username="bpp", password="password",
                       database="bpp") as pg:
        yield pg
```

Same shape for `make_redis`, `make_mysql`, `make_mongo`,
`make_rabbitmq`, plus `make_container(Cls, **kwargs)` as a generic
escape hatch.

### Why also "function-scoped" and "clean-session" fixture variants

The single most common pytest-isolation anti-pattern is "I want a
fresh container per test" (function-scoped container). That costs 3–8
seconds per test at container-start. The right answer 95% of the time
is "same container, fresh database" — but writing the
`CREATE DATABASE`/`DROP DATABASE` boilerplate yourself is annoying.
We ship that as a built-in (§4.4): `tc_psql_db`, `tc_mysql_db`,
`tc_mongo_db`, `tc_redis_clean`. Users get the fast path by default
and have to opt into the slow path explicitly (`tc_psql_func`).

### What this plugin is deliberately NOT

- NOT a `pytest-docker-compose` competitor. We drive
  `testcontainers-python` directly; no compose file.
- NOT a controller of `pytest_load_initial_conftests` timing. There is
  **no eager-start mode**, no env-var injection before user
  `conftest.py` loads, no `[tool.pytest-testcontainers]` controller-
  level config. That whole machinery moved to
  `pytest-testcontainers-django` (#2), which is the only project that
  actually needs it.
- NOT a reinvention of `testcontainers-python`'s wait strategies,
  health checks, or container internals. We pass through to its
  classes; we add `@pytest.fixture` decorators around them.

### PyPI name

`pytest-testcontainers` is currently free
(`https://pypi.org/pypi/pytest-testcontainers/json` → HTTP 404 as of
2026-05-08), as are `pytest_testcontainers`, `pytest-tc`,
`pytest-testcontainer`. We claim `pytest-testcontainers`.

### License

MIT, matching `testcontainers-python`. Confirmed before publishing.

---

## 2. Scope

### In scope

- Pytest plugin entry point (`pyproject.toml`
  `[project.entry-points.pytest11]`) that registers fixtures.
- Public maker functions (importable, not pytest-bound):
  `make_postgres`, `make_redis`, `make_mysql`, `make_mongo`,
  `make_rabbitmq`, `make_container`. Each returns a context manager.
- Default session-scoped fixtures per service: `tc_psql`, `tc_redis`,
  `tc_mysql`, `tc_mongo`, `tc_rabbitmq`.
- Function-scoped fixtures per service: `tc_psql_func`,
  `tc_redis_func`, `tc_mysql_func`, `tc_mongo_func`,
  `tc_rabbitmq_func`. Documented as "discouraged unless you really
  need this" (§5).
- Clean-session fixtures (session container + fresh state per test):
  `tc_psql_db`, `tc_mysql_db`, `tc_mongo_db`, `tc_redis_clean`. No
  clean-session for RabbitMQ — cleanup not obvious; user wires their own.
- Reuse-mode plumbing: stable per-worker container names, Ryuk
  disabled, port-conflict detection on restart of stopped containers.
  Activated globally via env (`PYTEST_TESTCONTAINERS_REUSE=1`) or CLI
  (`--testcontainers-reuse`).
- xdist-worker-aware container names: the worker id is one component
  (`{worker}`) of the templated name (§8.7), defaulting to `master`
  for non-xdist runs.
- Normalized `pytest.UsageError` when Docker daemon is unreachable
  (§7).
- `atexit` safety net for cleanup when `pytest_unconfigure` is skipped
  (process killed, fixture calls `sys.exit`, OOM).

### Out of scope (explicit non-goals)

- **Eager-start mode** of any kind. No `pytest_load_initial_conftests`
  hook beyond what's needed to register fixtures. No env-var
  injection. No "before django.setup()" magic. No pre-import of user
  `conftest.py`. → All of that lives in `pytest-testcontainers-django`
  (#2).
- **`baseline.sql` mounting / Postgres TEMPLATE wiring.** → Lives in
  `django-pg-baseline` (#3).
- **`docker-compose.yml` driving.** Use `pytest-docker-compose` /
  `pytest-docker` if that's what you want.
- **Per-test container start as the default.** It's available
  (`tc_<svc>_func`) but documented as a code smell (§5).
- **Health-checking beyond what `testcontainers-python`'s wait
  strategies already provide.** We pass through; we do not reinvent.
- **Kubernetes, Podman-rootless, remote Docker over SSH.** If
  `docker.from_env()` works, we work; otherwise out of scope.
- **Network creation between containers.** Each service we start is
  reachable from the host on its mapped port. That's enough for >95%
  of test setups including BPP. Use `pytest-docker-compose` if you
  need intra-network communication between testcontainers.
- **Tier 3 services in v1**: Kafka, Elasticsearch, vector DBs,
  Localstack. Users wire those themselves via `make_container()`. May
  be added in v1.x if demand justifies.

---

## 3. Architecture overview

### 3.1 Pure fixture model

Standard pytest plumbing. The plugin's `entry-points.pytest11` module
registers ~15 fixtures via `@pytest.fixture` and exports the maker
functions for `import pytest_testcontainers`. **No early hooks beyond
fixture registration. No env mutation. No conftest discovery dance.**

A user who only writes `def test_x(tc_psql): …` gets exactly what they
expect: a session-scoped Postgres container started lazily on first
request, stopped at session end (or kept alive in reuse mode).

### 3.2 What "lazy fixture" means here

`scope="session"` in pytest means "first request in the session
triggers `__enter__`; teardown on session finalization." For
testcontainers, that means:

- Container starts when the first test that requests `tc_psql` runs.
- Container is shared across all tests in that session.
- **Under pytest-xdist, "session" = "this worker's session"** (§6).
  With `-n 4`, you get 4 containers (one per worker). This is the
  single most surprising xdist fact and is documented prominently.

### 3.3 Layering

```
   user test code
        │
        │  (uses)
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  fixtures.py                                                 │
  │   tc_psql, tc_psql_func, tc_psql_db,                        │
  │   tc_redis, tc_redis_func, tc_redis_clean,                  │
  │   tc_mysql, …, tc_mongo, …, tc_rabbitmq, …                   │
  └──────────────────────────────────────────────────────────────┘
        │  (call)
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  makers.py                                                   │
  │   make_postgres, make_redis, make_mysql, make_mongo,         │
  │   make_rabbitmq, make_container                              │
  │     ─ build a testcontainers-python container                │
  │     ─ apply reuse name if PYTEST_TESTCONTAINERS_REUSE=1      │
  │     ─ ping daemon (one-shot, cached)                         │
  │     ─ register atexit cleanup                                │
  │     ─ return a context manager                               │
  └──────────────────────────────────────────────────────────────┘
        │  (delegates to)
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  testcontainers-python                                       │
  │   PostgresContainer, RedisContainer, MySqlContainer, …       │
  └──────────────────────────────────────────────────────────────┘
```

Maker layer holds the BPP-derived plumbing (`_check_docker_daemon`,
`_find_existing_container`, `_get_host_port`, reuse-name resolution,
Ryuk handling, atexit). Fixture layer is thin — `@pytest.fixture`
wrappers around `with make_xxx() as c: yield c`.

### 3.4 No plugin-managed config object

No `[tool.pytest-testcontainers]` table. No `ConfigError`. No TOML
parser. The handful of global toggles that DO exist (reuse mode,
project name for reuse-name prefix, daemon-ping skip) are read
straight from env vars / CLI flags at maker-call time. See §8.

---

## 4. Public API

### 4.1 Maker functions

For each supported service, ship a `make_<service>()` function
that returns a context manager wrapping a testcontainers-python class.

#### 4.1.1 Signatures

```python
from contextlib import AbstractContextManager
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.mysql import MySqlContainer
from testcontainers.mongodb import MongoDbContainer
from testcontainers.rabbitmq import RabbitMqContainer
from testcontainers.core.container import DockerContainer

def make_postgres(
    image: str = "postgres:16",
    *,
    username: str = "test",
    password: str = "test",
    database: str = "test",
    port: int = 5432,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs,
) -> AbstractContextManager[PostgresContainer]: ...

def make_redis(
    image: str = "redis:7-alpine",
    *,
    port: int = 6379,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs,
) -> AbstractContextManager[RedisContainer]: ...

def make_mysql(
    image: str = "mysql:8",
    *,
    username: str = "test",
    password: str = "test",
    database: str = "test",
    root_password: str = "test",
    port: int = 3306,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs,
) -> AbstractContextManager[MySqlContainer]: ...

def make_mongo(
    image: str = "mongo:7",
    *,
    username: str = "test",
    password: str = "test",
    port: int = 27017,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs,
) -> AbstractContextManager[MongoDbContainer]: ...

def make_rabbitmq(
    image: str = "rabbitmq:3-management",
    *,
    username: str = "guest",
    password: str = "guest",
    port: int = 5672,
    env: dict[str, str] | None = None,
    reuse_name: str | None = None,
    **kwargs,
) -> AbstractContextManager[RabbitMqContainer]: ...

def make_container(
    container_cls: type[DockerContainer],
    *args,
    reuse_name: str | None = None,
    **kwargs,
) -> AbstractContextManager[DockerContainer]: ...
```

#### 4.1.2 Return type contract

The context manager's `__enter__`:

1. Pings Docker daemon (`docker.from_env().ping()`). **Cache stores
   only successful pings**: a successful ping flips a process-wide
   flag and subsequent maker calls inside the same process skip the
   probe. A failed ping does NOT poison the cache — the next maker
   invocation re-runs the ping (cheap, ~5ms) so transient daemon
   restarts don't permanently disable us within the same process.
   Skipped entirely when `PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK=1`.
2. If `reuse_name` is set OR `PYTEST_TESTCONTAINERS_REUSE=1`:
   - Computes the effective name (§8.3).
   - Looks up by name. **Found-running**: skip `.start()`; bind a
     lightweight `DockerContainer` (or service-specific subclass)
     instance to the existing container ID so the standard
     `.get_container_host_ip()` / `.get_exposed_port()` API works
     against it. **Found-stopped** (status in `{exited, created,
     paused}`): call `container.start()` then `.reload()` to refresh
     `NetworkSettings.Ports`. **Not-found**: fall through to fresh
     start with `.with_name(<name>)` applied so the next run finds
     it.
3. Disables Ryuk if reuse mode (§8.5).
4. Calls `.start()` on the testcontainers-python container (skipped
   in the found-running branch above).
5. Registers an `atexit` callback that stops the container UNLESS
   reuse mode is on.
6. Returns the testcontainers-python instance directly. **No custom
   wrapper, in any mode** — the same `PostgresContainer` /
   `RedisContainer` / `DockerContainer` instance is yielded whether
   we just started a fresh container, restarted a stopped one, or
   bound to an already-running reused one. Test code calls
   `.get_container_host_ip()`, `.get_exposed_port()`, etc., exactly
   as it would against the upstream library. No custom `ServiceInfo`
   wrapper, no "lookup-mode handle" wrapper. This is the deliberate
   "we don't wrap; we add convenience" stance.

   No-op-stop in reuse mode is enforced at the maker's `__exit__`
   (§4.1.2 `__exit__`), not by intercepting `.stop()` on the
   instance. If user code manually calls `instance.stop()` inside a
   reuse-mode test it WILL stop the container — that's the user
   asking for it; the next run will simply restart it from
   `{exited}` state. Documented as "don't call `.stop()` yourself
   on session-scoped reuse containers; let the fixture's teardown
   handle lifecycle."

`__exit__`:

- Reuse mode: no-op.
- Function-scoped mode: `container.stop()`. Errors logged with
  `logger.exception(...)`, then swallowed (we're in cleanup; raising
  here masks the original test failure). The atexit fallback handles
  process-abrupt-exit cases.

#### 4.1.3 `**kwargs` pass-through

Every kwarg not consumed by the maker is forwarded to the
testcontainers-python class constructor. So `driver=` for
`PostgresContainer`, `dialect=` for `MySqlContainer`, etc. are
available without us shipping a per-service kwarg list. Type
checkers see `**kwargs: Any` — opinionated tradeoff for keeping the
maker signatures readable.

#### 4.1.4 `env=` parameter

Convenience for the common case "I want extra env vars on the
container". Translates to a sequence of
`container.with_env(k, v)` calls before `.start()`. Equivalent to
calling `.with_env()` directly on the result of `make_*()` if you
need the container instance pre-start (you don't — the maker calls
`.start()` for you).

#### 4.1.5 Generic `make_container`

Escape hatch for services we don't ship a maker for. Wraps any
`DockerContainer` subclass (or `DockerContainer` itself).

```python
from testcontainers.kafka import KafkaContainer
from pytest_testcontainers import make_container

@pytest.fixture(scope="session")
def kafka():
    with make_container(KafkaContainer, image="confluentinc/cp-kafka:7.5.0") as k:
        yield k
```

`reuse_name`, daemon-ping, atexit cleanup, Ryuk-disable-when-reuse —
all the maker-layer plumbing — applies. `args`/`kwargs` go to the
upstream constructor verbatim.

### 4.2 Default session fixtures

Session-scoped, lazily started on first request. One per service.
Naming convention: `tc_<short_service_name>`.

| Fixture | Maker call | Returns |
|---|---|---|
| `tc_psql` | `make_postgres()` | `PostgresContainer` |
| `tc_redis` | `make_redis()` | `RedisContainer` |
| `tc_mysql` | `make_mysql()` | `MySqlContainer` |
| `tc_mongo` | `make_mongo()` | `MongoDbContainer` |
| `tc_rabbitmq` | `make_rabbitmq()` | `RabbitMqContainer` |

**Naming note:** Postgres fixture is `tc_psql` (NOT `tc_postgres`).
User-confirmed. The maker stays `make_postgres` because it returns a
`PostgresContainer` — staying aligned with the upstream class name.
The fixture name is shorter because users type it on every test.

**User override:** All `tc_*` fixtures are plain
`@pytest.fixture(scope="session")` definitions registered by the
plugin's entry point. Standard pytest fixture-overriding rules
apply: a user can redefine `tc_psql` (or any other) in their own
`conftest.py` to swap image/credentials/options, and pytest's
nearest-conftest resolution takes the user's version. No special
opt-in or de-registration is needed; the plugin's fixture is
silently shadowed. This is the recommended pattern when you want
*one* customized PG everywhere instead of writing a new fixture
name like `bpp_pg`.

### 4.3 Function-scoped fixtures

Fresh container per test, killed after each test. Same maker, same
defaults. Naming: `tc_<service>_func`.

| Fixture | Scope | Cost per test |
|---|---|---|
| `tc_psql_func` | function | ~3–8s (container start) |
| `tc_redis_func` | function | ~0.5–2s |
| `tc_mysql_func` | function | ~5–15s |
| `tc_mongo_func` | function | ~2–5s |
| `tc_rabbitmq_func` | function | ~3–8s |

Documented prominently as "you almost certainly don't want this — see
§5". The fixture exists so users have a one-line option for the
genuinely-needed case (testing the testcontainer-machinery itself,
or DDL that corrupts container-level state).

### 4.4 Clean-session fixtures (session container + fresh state per test)

Function-scoped, but the underlying container is reused across the
session — only the database/keyspace state is fresh.

| Fixture | Scope | Container | Per-test cost |
|---|---|---|---|
| `tc_psql_db` | function | session-scoped `tc_psql` | ~50–200ms (`CREATE DATABASE`) |
| `tc_mysql_db` | function | session-scoped `tc_mysql` | ~100–500ms (`CREATE DATABASE`) |
| `tc_mongo_db` | function | session-scoped `tc_mongo` | ~10–50ms (new `db.<name>`) |
| `tc_redis_clean` | function | session-scoped `tc_redis` | ~5–20ms (`FLUSHALL`) |

Behavior:

- **`tc_psql_db`**: yields a connection-info object (host, port, user,
  password, `dbname=f"test_{secrets.token_hex(6)}"` — 12 hex chars,
  ~6e13 unique values per session, more than enough for any
  conceivable test count). Creates `CREATE DATABASE "test_<hex>"`
  against the session container's `postgres` admin DB before the
  test, drops it (`DROP DATABASE … WITH (FORCE)`) after. See §4.6
  for the auto-detect admin-command strategy (psycopg fast path vs.
  `docker exec` fallback). Connection itself for the test code
  remains the user's responsibility — `tc_psql_db` only yields conn
  info, never a live `psycopg.Connection`.
- **`tc_mysql_db`**: same shape, MySQL-flavored
  `` CREATE DATABASE `test_<hex>` `` / `DROP DATABASE`, with the
  same `secrets.token_hex(6)` naming. Same auto-detect strategy
  (pymysql fast path vs. `docker exec mysql -e ...`).
- **`tc_mongo_db`**: yields a connection-info object with a fresh
  database name. Drops via `db.dropDatabase()` after the test. Same
  auto-detect strategy (pymongo fast path vs. `docker exec mongosh
  --eval ...`). Mongo doesn't need explicit creation — first write
  materializes the DB.
- **`tc_redis_clean`**: yields the `RedisContainer`. After the test,
  runs `FLUSHALL` (clears all 16 logical DBs, not just db 0) via
  `container.exec(["redis-cli", "FLUSHALL"])`. **Always uses
  `docker exec`, never the Python client** — opening a TCP
  connection to issue one `FLUSHALL` is more overhead than just
  exec-ing the in-container CLI. See §4.6. Cleanup error is surfaced
  (logged + re-raised) so a flush failure doesn't silently corrupt
  the next test.
- **No `tc_rabbitmq_*_clean`**: RabbitMQ has queues, exchanges,
  bindings, vhosts, federation, shovel state — "clean state" doesn't
  have one obvious meaning. Users wire their own using `tc_rabbitmq`
  + their own teardown.

The connection-info object for `tc_psql_db` / `tc_mysql_db` /
`tc_mongo_db` is a small frozen dataclass:

```python
@dataclass(frozen=True)
class DbConnInfo:
    host: str
    port: int
    username: str
    password: str
    database: str

    def url(self, scheme: str = "postgresql") -> str:
        return f"{scheme}://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"
```

Same shape across all three DB clean-session fixtures; only `scheme=` default
differs.

### 4.5 Full service × variant table

| Service | Maker | Session fixture | Function-scoped fixture | Clean-session fixture |
|---|---|---|---|---|
| Postgres | `make_postgres` | `tc_psql` | `tc_psql_func` | `tc_psql_db` |
| Redis | `make_redis` | `tc_redis` | `tc_redis_func` | `tc_redis_clean` |
| MySQL | `make_mysql` | `tc_mysql` | `tc_mysql_func` | `tc_mysql_db` |
| MongoDB | `make_mongo` | `tc_mongo` | `tc_mongo_func` | `tc_mongo_db` |
| RabbitMQ | `make_rabbitmq` | `tc_rabbitmq` | `tc_rabbitmq_func` | — |
| (any) | `make_container(Cls, **kw)` | — | — | — |

This table appears verbatim in the README.

### 4.6 Admin-command strategy: auto-detect Python client, fallback to `docker exec`

Clean-session fixtures need to issue admin commands (`CREATE DATABASE`,
`DROP DATABASE`, `dropDatabase()`, `FLUSHALL`). The total surface
across all clean-session fixtures is **six single-shot commands** — no queries
returning rows, no transactions, no schema introspection, no async.

Two viable execution paths:

1. **Python client fast path** — `psycopg` / `pymysql` / `pymongo`
   imported on the host, opens TCP to the container, issues SQL,
   closes. Per-call cost: ~5ms.
2. **`docker exec` fallback** — invoke the container's own CLI
   (`psql -c '…'`, `mysql -e '…'`, `mongosh --eval '…'`,
   `redis-cli FLUSHALL`) via `container.exec(...)`. Zero host-side
   client deps. Per-call cost: ~50–100ms.

**Strategy: auto-detect by import. No flag, no extra config.**

```python
# Pseudo-code, real impl per service:
def _create_postgres_db(container, name):
    try:
        import psycopg  # noqa: F401
    except ImportError:
        _warn_once("docker_exec_fallback_postgres")
        return _create_via_docker_exec(container, name)
    return _create_via_psycopg(container, name)
```

**Postgres fast-path requires autocommit.** `CREATE DATABASE` and
`DROP DATABASE` cannot run inside a transaction block in PostgreSQL.
psycopg3 defaults to `autocommit=False` (transactions opened
implicitly), so a naive `conn.execute("CREATE DATABASE ...")` raises
`ActiveSqlTransaction`. The fast-path implementation opens the
admin connection with `autocommit=True` and uses
`psycopg.sql.Identifier` for the DB name (defensive quoting; the
name is generated by us via `secrets.token_hex`, costs nothing).
`DROP DATABASE … WITH (FORCE)` uses the same autocommit connection.

MySQL's fast path (pymysql) has no autocommit gotcha — `CREATE
DATABASE` auto-commits regardless. Mongo fast path uses
`MongoClient(...).drop_database(name)`, no SQL involved.

- Default install (no extras): all clean-session fixtures use `docker exec`. Works
  out of the box, zero deps.
- User installs `psycopg[binary]` / `pymysql` / `pymongo` (directly
  or via the convenience extra `pytest-testcontainers[clients]`):
  next session automatically picks the fast path. Zero code change
  in the user's tests.

**Fallback warning** (one-shot per fixture-type per session):

```
[pytest-testcontainers] tc_psql_db using docker exec fallback
(~100ms per CREATE/DROP DATABASE). For ~20× speedup install the
Postgres client:
    pip install psycopg[binary]
or via the convenience extra:
    pip install pytest-testcontainers[clients]
Suppress this notice with PYTEST_TESTCONTAINERS_QUIET=1.
```

Issued the first time a clean-session fixture for that service runs in the
session. Suppressible globally via `PYTEST_TESTCONTAINERS_QUIET=1`.
Per-fixture-type so a project using only `tc_psql_db` doesn't see
warnings about pymongo it never invoked.

**Why no flag in v1.** A `clean_session_strategy = "auto" | "exec" | "client"`
config knob is conceivable but premature. Auto-detect by import is
deterministic and debuggable (`pip list | grep psycopg` answers
"which path will run"). Forcing exec when client is installed is a
rare edge case (testing the fallback codepath itself, mostly); a
follow-up if anyone asks.

**Why Redis is exec-only.** Opening a TCP connection from `redis-py`
to issue a single `FLUSHALL` involves: connect → AUTH (if password)
→ FLUSHALL → close — multiple round-trips. `docker exec redis-cli
FLUSHALL` is one round-trip (the in-container client uses the local
socket). For Redis specifically the cost-benefit inverts: exec is
*faster* than the Python client. Exec-only, no auto-detect.

**Errors**: docker-exec path captures `(exit_code, stdout, stderr)`
from `container.exec_run(..., demux=True)`. Non-zero exit raises
`CleanSessionFixtureError(command=..., stderr=..., exit_code=...)`.
Python-client path catches the client-library exception and
re-raises it as `CleanSessionFixtureError(command=..., stderr=repr(exc),
exit_code=None)` — see §9.1 for the dataclass shape — so user code
catches a single exception type regardless of which path ran. Both
surface clearly in the pytest report.

**Redis cleanup error policy** (referenced in §4.4): a non-zero
exit from `redis-cli FLUSHALL` re-raises as `CleanSessionFixtureError`
during fixture teardown — fail loudly rather than silently leak
state into the next test.

### 4.7 Optional extras: convenience for the fast path

`pyproject.toml` declares optional dependency groups so users who
want all fast paths in one shot can install them together:

```toml
[project.optional-dependencies]
clients-postgres = ["psycopg[binary] >=3.1"]
clients-mysql    = ["pymysql >=1.1"]
clients-mongo    = ["pymongo >=4.6"]
clients         = [
    "pytest-testcontainers[clients-postgres,clients-mysql,clients-mongo]",
]
# No clients-redis — Redis is always exec-based (see §4.6).
```

These are **purely opt-in convenience**. The base install works
fully — extras only switch clean-session fixtures to the fast path. No fixture
fails if extras aren't installed; the warning hints at the upgrade
path.

### 4.8 What we require from in-container CLI tools

Documented contract for which binaries we expect inside the
container image when the `docker exec` fallback runs. Surface is
deliberately tiny:

| Service  | Required binary | Invocation                                       |
|----------|-----------------|--------------------------------------------------|
| Postgres | `psql`          | `psql -U <user> -d postgres -c '<SQL>'` (PGPASSWORD env) |
| MySQL    | `mysql`         | `mysql -u <user> -p<pass> -e '<SQL>'`            |
| Mongo    | `mongosh`       | `mongosh "<uri>" --eval '<JS>'` (Mongo 5+)       |
| Redis    | `redis-cli`     | `redis-cli [-a <pass>] FLUSHALL`                 |

All official upstream images ship these clients (`postgres:*`,
`mysql:*`, `mongo:*`, `redis:*`). Custom slim images may strip
them — if your image does, write your own fixture composing
`make_<service>` plus your preferred client. Document this caveat
in the README "When the fallback won't work" section.

`mongosh` (Mongo 5+) replaces the legacy `mongo` shell. We require
`mongosh`; Mongo 4.x and earlier are not supported in the exec
fallback (the fast path via `pymongo` still works against any
version `pymongo` itself supports).

---

## 5. Image overrides — concrete examples

These examples MUST appear in the README (this is the source of
truth for what gets copy-pasted there).

```python
import pytest
from pytest_testcontainers import make_postgres

# Default (postgres:16, test/test/test).
# Just use the built-in fixture:
def test_default(tc_psql):
    ...  # tc_psql is already the PostgresContainer

# Postgres 18.
@pytest.fixture(scope="session")
def pg_18():
    with make_postgres(image="postgres:18") as pg:
        yield pg

# Specific minor version (pinning for reproducibility).
@pytest.fixture(scope="session")
def pg_old():
    with make_postgres(image="postgres:16.13") as pg:
        yield pg

# Custom user-built image (e.g. one with extensions baked in).
@pytest.fixture(scope="session")
def bpp_psql():
    with make_postgres(image="iplweb/bpp-dbserver:psql-16.13") as pg:
        yield pg

# Custom image + extra env (image-specific tuning knobs).
@pytest.fixture(scope="session")
def bpp_psql_fast():
    with make_postgres(
        image="iplweb/bpp-dbserver:psql-16.13",
        env={
            "POSTGRESQL_UNSAFE_BUT_FAST": "1",
            "POSTGRESQL_MAX_LOCKS_PER_TRANSACTION": "512",
        },
    ) as pg:
        yield pg

# Custom credentials (must match what the app expects).
@pytest.fixture(scope="session")
def pg_bpp_creds():
    with make_postgres(
        image="iplweb/bpp-dbserver:psql-16.13",
        username="bpp",
        password="password",
        database="bpp",
    ) as pg:
        yield pg
```

Same pattern for `make_redis`, `make_mysql`, `make_mongo`,
`make_rabbitmq`. The point is: **swapping the image is a one-line
change**, the rest of the test code is unchanged because the maker
returns a vanilla `testcontainers-python` instance with the same API.

### Combined with clean-session pattern

```python
import pytest, secrets
from pytest_testcontainers import make_postgres, DbConnInfo

@pytest.fixture(scope="session")
def bpp_pg():
    with make_postgres(
        image="iplweb/bpp-dbserver:psql-16.13",
        username="bpp", password="password", database="bpp",
    ) as pg:
        yield pg

@pytest.fixture
def bpp_pg_db(bpp_pg):
    """Fresh DB per test on top of session-scoped custom-image PG."""
    name = f"test_{secrets.token_hex(6)}"
    admin = DbConnInfo(
        host=bpp_pg.get_container_host_ip(),
        port=int(bpp_pg.get_exposed_port(5432)),
        username="bpp", password="password", database="postgres",
    )
    # admin connection / CREATE DATABASE / DROP DATABASE wiring;
    # see "Clean-session pattern walkthrough" in the README for the
    # full implementation.
    ...
```

The built-in `tc_psql_db` does exactly this against the default
`tc_psql`. Users who need a custom-image variant write a parallel
fixture.

---

## 6. Container scope: when to use what

This is the educational core of the README. The user choices, in
order of decreasing preference:

### 6.1 Default: session fixture (`tc_psql`)

One container per worker, shared across all tests on that worker.
Cheapest, fastest. The right default.

Use for:
- Read-heavy tests.
- Tests that wrap their work in transactions and roll back
  (pytest-django's `db` fixture; SQLAlchemy savepoints).
- Tests that delete their own rows in teardown.

Don't use for:
- DDL-heavy tests (CREATE TABLE / ALTER TABLE / CREATE EXTENSION)
  unless rolled back inside a transaction.
- Tests that mutate global server state (`ALTER SYSTEM SET …`,
  replication slots, prepared statements that persist).

### 6.2 Clean-session: session container + fresh DB (`tc_psql_db`)

Same container as §6.1, but each test gets a brand-new database
created and dropped around it.

Use for:
- DDL-heavy suites (Django migration tests; schema-change tests).
- Tests that vacuum, reset sequences with autocommit, or do anything
  else that fights with transactional isolation.
- Replication slot / `pg_stat_*` tests where you want a clean view.

Cost: ~50–200ms per test for `CREATE DATABASE` / `DROP DATABASE`. Two
orders of magnitude faster than per-test container start.

This is what most users who think they need §6.3 actually want.

### 6.3 Function-scoped container (`tc_psql_func`)

Fresh container per test. Available, **strongly discouraged**.

The math, with concrete numbers:

> **Container start = 3–5 seconds for Postgres.**
> **100 tests × 5s = 8 minutes wasted on container startup.**
> **The clean-session pattern (§6.2) gives the same isolation ~480× faster**
> (50ms per fresh DB vs 5s per fresh container).

Use ONLY when:

1. Test mutates global server config that can't be reset
   (`ALTER SYSTEM SET …` followed by a restart requirement).
2. Test corrupts container-level state (broken cluster, FS
   corruption, killed `postmaster`, etc.).
3. You're testing the testcontainer machinery itself.

If your reasoning is "I want isolation between tests," that's §6.2,
not §6.3.

### 6.4 What about a single shared DB + transaction rollback?

(Sometimes called "Variant B".) **Not shipped, not recommended.**

Doesn't work for:
- DDL tests (migrations, schema changes) — DDL implicit-commits in
  many SQL dialects, breaking the rollback-around-each-test model.
- Vacuum, sequence operations with autocommit.
- Replication state.
- Anything cross-connection.

User-confirmed "wariant B nie chce" — we don't ship a fixture for
this pattern.

### 6.5 README cross-reference

The README's "Container scope: when to use what" section MUST
reproduce the four-option ladder above (Default / Clean-session / Function-scoped
/ Variant-B-rejected) with the 480× math, the concrete-numbers
version of "100 tests × 5s = 8 minutes," and the three legitimate use
cases for §6.3.

---

## 7. xdist behavior

### 7.1 Surprising thing #1: `scope="session"` is per-worker

This is the single most common gotcha and MUST be flagged in the
README under that exact header.

Under `pytest-xdist`, each worker process runs its own pytest
session. `@pytest.fixture(scope="session")` therefore means "one per
worker", NOT "one for the whole pytest invocation". With `-n 8`, you
get **8 containers**, one per worker.

Many users assume the opposite (because "session" sounds like
"global"). It isn't.

### 7.2 Worked example for the README

```
$ pytest -n 4 tests/                   # 4 workers
[gw0] container started: postgres on localhost:54321
[gw1] container started: postgres on localhost:54322
[gw2] container started: postgres on localhost:54323
[gw3] container started: postgres on localhost:54324
```

If you want a single container shared across all workers, you cannot
use this plugin's session fixtures alone — that requires the
controller to start the container BEFORE workers fork and to inject
host/port via env vars. That's exactly the eager-start machinery that
moved to `pytest-testcontainers-django` (#2). For non-Django users
who genuinely need cross-worker container sharing, the workaround is
to start the container manually outside pytest, pass connection info
through environment variables, and use `--no-testcontainers` (§8.2)
to disable our `tc_*` fixtures (which would otherwise try to start
their own per-worker containers). Then write your own fixture that
reads the env vars set by the wrapper script.

Example:

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
import os
import pytest

@pytest.fixture(scope="session")
def shared_pg():
    return {
        "host": os.environ["SHARED_PG_HOST"],
        "port": int(os.environ["SHARED_PG_PORT"]),
        "username": "postgres",
        "password": "test",
    }
```

We don't ship this orchestration; it's out of scope for the plugin
(it's a 12-line shell snippet specific to your CI / dev setup). The
spec mentions it so users who hit "I really need one container for
all 8 workers" know the supported escape hatch and aren't tempted to
hack around `tc_psql` internals.

### 7.3 Worker ID resolution

```python
worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
# "gw0", "gw1", …, or "master" when not running under xdist.
```

The plugin uses this for per-worker reuse-mode names (§8.3) and
exposes it to user code via the standard pytest-xdist `worker_id`
fixture (which we don't redefine — it's already in pytest-xdist).

### 7.4 Per-worker reuse-mode names

Container names embed the xdist worker id via the `{worker}` template
component (§8.7), so each worker gets its own container. With `-n 8` the
names differ only in that trailing component (the shared
project/service/branch/dirtag prefix is shown here as `…`):

```
…-gw0
…-gw1
…
…-gw7
```

For non-xdist runs it is `master` (e.g. `…-master`).

Subtlety: a previous run with `-n 8 --testcontainers-reuse` leaves 8
named containers. A subsequent `-n 4` reuses 4 of them and ignores
the others. They linger until manual cleanup
(`pytest --testcontainers-clean` is the CLI sugar; under the hood
the plugin calls docker-py to enumerate matching containers and
removes them one by one — equivalent to
`docker ps -aq --filter name=<project>-tc- | xargs -r docker rm -f`,
where `xargs -r` (`--no-run-if-empty` on GNU) skips invocation when
the filter matches nothing. The plugin's Python implementation
trivially handles the empty-list case via
`for c in client.containers.list(all=True, filters=...): c.remove(force=True)`,
so this shell sketch is just for users who want to do it by hand.).
We don't auto-clean because a concurrent pytest invocation might be
using them.

---

## 8. Configuration surface (env + CLI only)

No TOML config table. The handful of toggles read at maker-call time:

### 8.1 Environment variables

| Name | Effect |
|---|---|
| `PYTEST_TESTCONTAINERS=0` | Disable plugin fixtures only. Each `tc_*` fixture is replaced at registration time with a stub that raises `UsageError("--no-testcontainers; user expected to provide service externally")`. Maker functions called from non-pytest code (scripts, REPL — see §8.6) are unaffected. Accepts `0`/`false`/`no`. |
| `PYTEST_TESTCONTAINERS_REUSE=1` | Reuse mode (§8.5). |
| `PYTEST_TESTCONTAINERS_PROJECT` | Override the project name used in reuse names (§8.3). Default: derived from `pyproject.toml [project].name` if a `pyproject.toml` is reachable from `Path.cwd()` walking up; else `"pytest-tc"` with a one-shot stderr warning at first maker invocation (§8.3). |
| `PROJECT_NAME` | Generic project-name source, below `PYTEST_TESTCONTAINERS_PROJECT` and above `COMPOSE_PROJECT_NAME` (§8.3). |
| `COMPOSE_PROJECT_NAME` | docker-compose's standard project var, below `PROJECT_NAME` (§8.3). |
| `PYTEST_TESTCONTAINERS_NAME_TEMPLATE` | Override the container-name template (§8.7). |
| `PYTEST_TESTCONTAINERS_NO_DAEMON_CHECK=1` | Skip Docker `ping()` (rare, for debugging in environments where ping returns false-negatives). |
| `PYTEST_TESTCONTAINERS_QUIET=1` | Suppress all one-shot stderr advisory notices (docker-exec fallback warning §4.6, project-name fallback warning §8.3). Errors still surface; only informational tips are silenced. |

### 8.2 CLI flags

| Flag | Effect |
|---|---|
| `--no-testcontainers` | Same as `PYTEST_TESTCONTAINERS=0`. |
| `--testcontainers-reuse` | Same as `PYTEST_TESTCONTAINERS_REUSE=1`. |
| `--testcontainers-no-reuse` | Force function-scoped mode regardless of env. |
| `--testcontainers-project=NAME` | Override `PYTEST_TESTCONTAINERS_PROJECT`. |
| `--testcontainers-name-template=T` | Override the container-name template (§8.7). Highest precedence. |
| `--testcontainers-clean` | Stop+remove all `<project>-tc-*` containers and exit pytest with code 0. |

Precedence: CLI > env > defaults.

### 8.3 Project name resolution (for reuse names)

The `{project}` placeholder in the container-name template (§8.7) resolves
as:

1. CLI `--testcontainers-project=NAME` if given.
2. Env `PYTEST_TESTCONTAINERS_PROJECT` if set.
3. Env `PROJECT_NAME` if set.
4. Env `COMPOSE_PROJECT_NAME` if set.
5. `pyproject.toml [project].name` from the nearest `pyproject.toml`
   walking up from `Path.cwd()` (`tomllib` ≥3.11; `tomli` on 3.10).
6. The cwd directory basename.
7. Fallback literal `"pytest-tc"` (only when the cwd basename is empty,
   i.e. the filesystem root) — this is what triggers the one-shot advisory.

Justification for using `pyproject.toml [project].name` as default:
- The project already names itself there for PyPI; reusing it avoids
  asking the user to repeat themselves.
- Different projects on the same dev machine get different prefixes
  automatically → no monorepo collision in reuse mode.
- We do NOT need a `[tool.pytest-testcontainers]` table; we read a
  field from a section pytest already opens for its own config. Zero
  marginal complexity.

The one drawback: a project without a `pyproject.toml [project].name`
falls back to literal `pytest-tc`, which can collide between
projects. Users who hit this set `PYTEST_TESTCONTAINERS_PROJECT=…`
explicitly.

**Fallback warning**: when path 4 (`"pytest-tc"` literal) is reached,
the plugin emits a one-shot stderr message at the first maker
invocation in the session:

```
[pytest-testcontainers] using fallback project name 'pytest-tc' for
reuse-name prefix (no [project].name found in pyproject.toml).
Tip: set PYTEST_TESTCONTAINERS_PROJECT=<name> to avoid collisions
with other projects on the same machine.
Suppress this notice with PYTEST_TESTCONTAINERS_QUIET=1.
```

One-shot per process. Suppressible via `PYTEST_TESTCONTAINERS_QUIET=1`
(same env var as the docker-exec fallback warning in §4.6 — single
quiet switch, not per-message). Not emitted when reuse mode is off
*and* no maker passes `reuse_name=` explicitly, since project-name
sanitization (§8.4) is computed lazily and unused names never
trigger the warning.

### 8.4 Container name sanitization

Reuse names are passed straight to Docker via `.with_name(<name>)`.
Docker's container-name regex is
`[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}`. PyPI project names (which we
read for the `<project>` component, §8.3) allow `.`, `-`, `_` and
case-insensitive letters/digits — all already legal in Docker names
*except* for the leading-character constraint and casing.

Sanitization, applied to both the resolved `<project>` and the
`<service>` and `<worker_id>` components before composition:

```python
import re

def _sanitize(component: str) -> str:
    # 1. lowercase (Docker is case-sensitive but lowercase is the
    #    convention; matches docker-compose project naming).
    s = component.lower()
    # 2. replace any character outside [a-z0-9_.-] with '-'.
    s = re.sub(r"[^a-z0-9_.-]", "-", s)
    # 3. collapse runs of '-' to a single '-'.
    s = re.sub(r"-+", "-", s)
    # 4. strip leading non-alphanumerics (Docker requires the first
    #    char to be in [a-zA-Z0-9]).
    s = re.sub(r"^[^a-z0-9]+", "", s)
    # 5. strip trailing '-'/'.'/'_' for cosmetic reasons.
    s = s.rstrip("-._")
    return s or "x"  # never return empty
```

Concrete cases:

| Input              | Output         | Notes                              |
|--------------------|----------------|------------------------------------|
| `MyProject`        | `myproject`    | lowercased                         |
| `my_project`       | `my_project`   | underscore preserved               |
| `my.project`       | `my.project`   | dot preserved                      |
| `my project`       | `my-project`   | space → `-`                        |
| `foo/bar`          | `foo-bar`      | slash → `-`                        |
| `_internal`        | `internal`     | leading underscore stripped        |
| `123abc`           | `123abc`       | leading digit OK (Docker allows)   |
| `--`               | `x`            | empty-after-strip → safe fallback  |
| `acme.corp/widget` | `acme.corp-widget` | mixed                          |

`<service>` is one of our hard-coded short names (`psql`, `redis`,
`mysql`, `mongo`, `rabbitmq`) for built-in fixtures, or derived
from `Cls.__name__` for `make_container` (§16 #5) — both already
safe but pass through `_sanitize` defensively.

`<worker_id>` from pytest-xdist is `master` or `gw<n>` (n in
0..255), already safe.

After rendering, the full name is bounded by Docker's 255-char limit.
Components are not individually capped; the plugin sanitizes the whole
rendered name and, if it exceeds 255 chars, truncates to 246 and appends
an 8-char hex hash of the *full untruncated* name to keep uniqueness
(`finalize_name`):

```python
def finalize_name(name: str) -> str:
    s = sanitize_component(name)
    if len(s) <= 255:
        return s
    h = hashlib.blake2s(name.encode(), digest_size=4).hexdigest()  # 8 chars
    return s[:246] + "-" + h
```

Branch names make long names more likely than the old worker-only
suffix, so this guard matters more than it once did.

### 8.5 Reuse mode mechanics

When reuse is on:

1. **Ryuk is disabled lazily on the first maker `__enter__`**, BEFORE
   that maker calls `.start()` on its container. Concretely: the
   maker layer reads the reuse decision (CLI > env > defaults), and
   if reuse is on, sets
   `testcontainers.core.config.testcontainers_config.ryuk_disabled
   = True` exactly once per process (idempotent guard) before
   delegating to `container.start()`. Otherwise the next pytest run
   with Ryuk enabled would reap our named containers — defeating
   reuse.

   **Why lazy is correct (no `pytest_configure` hook needed):**
   `testcontainers-python` does NOT spawn the Ryuk reaper at import
   time; Ryuk is started on first `DockerContainer.start()` call
   that doesn't have `ryuk_disabled = True` set. Setting the flag
   any time before the first `.start()` is sufficient. Doing it in
   the maker (rather than a `pytest_configure` hook) keeps §3.1's
   "no early hooks beyond fixture registration" promise intact, and
   makes the makers usable from non-pytest contexts (§8.6) with the
   same semantics.

   **What about already-running Ryuk in the same process?** Not
   possible in normal use — pytest sessions don't import
   `testcontainers` and start a container before any maker runs. If
   a user does this manually (imports testcontainers, starts a
   container outside our makers, *then* calls our maker with reuse
   mode), Ryuk is already up and we cannot retract it. Documented
   as "if you mix raw `testcontainers-python` usage with reuse-mode
   `pytest-testcontainers`, set `TESTCONTAINERS_RYUK_DISABLED=true`
   yourself before importing `testcontainers`."
2. Each maker's `__enter__` looks up the container by its computed
   name:
   - **Not found**: start fresh with `.with_name(<name>)` applied.
   - **Found, status=running**: return the existing container; don't
     start a new one. Stop is a no-op at `__exit__`.
   - **Found, status in {exited, created, paused}**: call
     `container.start()`, then `.reload()` to refresh
     `NetworkSettings.Ports`. If start fails (typically host-port
     conflict because something else now holds the previously-mapped
     port), raise `pytest.UsageError` with `docker rm -f <name>`
     remediation.
3. `atexit` callback is NOT registered for reuse-mode containers.
4. The "freshly-started-with-name" case still persists at exit
   (first-run-with-reuse leaves the container alive for the next run,
   matching BPP's existing semantics).

### 8.6 Using makers outside a pytest session

Maker functions (`make_postgres`, `make_redis`, `make_mysql`,
`make_mongo`, `make_rabbitmq`, `make_container`) are plain
context-manager factories. They are importable and callable from
**any Python code**, not only pytest fixtures: dev scripts, REPL
sessions, `manage.py` subcommands (e.g. BPP's `runserver` flow),
notebooks, conftest module bodies, etc.

Behavior outside pytest:

- **Daemon ping** runs the same way (cached on success, §4.1.2 step
  1). Fails the same way (`DockerNotRunningError` →
  `pytest.UsageError`; outside pytest the `UsageError` propagates as
  a normal exception — pytest just catches it nicely, but the
  exception class itself works fine standalone).
- **Reuse mode** is read from env (`PYTEST_TESTCONTAINERS_REUSE`)
  and from the explicit `reuse_name=` kwarg. CLI flags (§8.2) only
  apply during pytest; from a script, set the env var (or pass
  `reuse_name=` explicitly).
- **Project name resolution** (§8.3) walks up from `Path.cwd()` for
  `pyproject.toml` exactly the same way. From a script run in a
  random `/tmp` dir, this falls back to literal `pytest-tc` — set
  `PYTEST_TESTCONTAINERS_PROJECT` if you care.
- **Worker-id component** (§7.3) defaults to `master` outside
  pytest-xdist (the env var is unset), so the name's `{worker}`
  component is `master`. A script and a pytest run on the
  same machine without `-n` will share that container; with `-n N`
  they don't (workers use `gw0..gwN-1`). This is desired — it lets
  a `manage.py runserver` reuse the same container that `pytest`
  (no xdist) just left running.
- **`atexit` cleanup**: registers normally for function-scoped mode, skips
  for reuse mode. Same semantics as inside pytest.
- **`PYTEST_TESTCONTAINERS=0`**: only affects the *fixture stubs*
  (which don't exist outside pytest). Maker functions ignore it.
  Rationale: a script that calls `make_postgres()` did so deliberately;
  silently no-op'ing because of an env var would be confusing.

Example (BPP-style `manage.py runserver` warm-start):

```python
# scripts/dev_db.py
import os
os.environ["PYTEST_TESTCONTAINERS_REUSE"] = "1"

from pytest_testcontainers import make_postgres

with make_postgres(image="iplweb/bpp-dbserver:psql-16.13",
                   username="bpp", password="password",
                   database="bpp") as pg:
    print(f"PG ready at {pg.get_container_host_ip()}:"
          f"{pg.get_exposed_port(5432)}")
    # ... start dev server, etc.
```

This is fully supported and will not be broken in v1.x.

### 8.7 Container name template

Every container the plugin starts — in **both** default and reuse mode — is
named by expanding a template and passing the result through `finalize_name`
(§8.4 sanitization + 255-char trim/checksum).

Default template: `{project}-tc-{service}-{branch}-{dirtag}-{worker}`.

Placeholders: `{project}` (§8.3), `{service}` (slug), `{branch}`
(`.git/HEAD`, no subprocess; detached → 12-char SHA; no repo → empty),
`{dir}` (worktree-root basename), `{dirtag}` (8-hex blake2s of the
worktree-root absolute path), `{worker}` (xdist id), `{rand}`
(`secrets.token_hex(2)` — empty in reuse mode for byte-stable find-or-create;
in default mode auto-appended when the template omits it). Empty placeholders
collapse with no dangling dashes. An unknown placeholder raises
`NameTemplateError`.

Template precedence: `--testcontainers-name-template` >
`PYTEST_TESTCONTAINERS_NAME_TEMPLATE` >
`pyproject.toml [tool.pytest_testcontainers].name_template` > built-in default.

Reuse-mode names are now per-(project, service, branch, directory, worker);
switching branch/worktree creates a new container and leaves the old one until
`--testcontainers-clean`. The maker `reuse_name=` argument still overrides the
whole scheme (sanitized verbatim, not templated).

---

## 9. Error handling

All user-facing errors raise `pytest.UsageError` with a human-readable
message. Internal errors keep the original exception chained
(`raise … from exc`). Per CLAUDE.md (BPP) and global rules: NO bare
`except: pass`, NO silent swallowing.

### 9.1 Exception classes (`pytest_testcontainers.errors`)

```python
class PytestTestcontainersError(RuntimeError):
    """Base. Plugin-internal; user code rarely sees this."""

class DockerNotRunningError(PytestTestcontainersError):
    """`docker.from_env().ping()` failed."""

class ContainerStartError(PytestTestcontainersError):
    """A container failed `.start()`. Wraps testcontainers exc."""

class ReuseConflictError(PytestTestcontainersError):
    """An existing reused container exists but cannot be brought
    back to running (port conflict, image mismatch, …)."""

class CleanSessionFixtureError(PytestTestcontainersError):
    """A clean-session fixture (`tc_psql_db`, `tc_mysql_db`, `tc_mongo_db`,
    `tc_redis_clean`) failed to issue an admin command (CREATE/DROP
    DATABASE, dropDatabase(), FLUSHALL).

    Carries:
        command: str             # the SQL/JS string or shell argv
        stderr:  str             # captured stderr (docker-exec path)
                                 # or repr(exc) (Python-client path)
        exit_code: int | None    # docker-exec only; None for client path

    Raised from BOTH execution paths so user code sees a single
    exception type regardless of whether the fast path or the
    fallback ran.
    """
```

Note: no `ConfigError` — there's no config file to be malformed.

### 9.2 User-visible mapping

Each internal exception is caught at the maker `__enter__` boundary
and converted to `pytest.UsageError`:

- `DockerNotRunningError` →

  ```
  [pytest-testcontainers] Docker daemon is not reachable.
  Is Docker Desktop / colima / Rancher Desktop running?

  Options:
    - start it and re-run pytest, OR
    - disable the plugin: --no-testcontainers
      (or PYTEST_TESTCONTAINERS=0), OR
    - point at remote Docker via DOCKER_HOST.

  Underlying error: <exc>
  ```

- `ReuseConflictError` →

  ```
  [pytest-testcontainers] Cannot start existing container '<name>': <exc>.

  Most common cause: another process now holds the port this
  container was previously bound to. To start fresh:
      docker rm -f <name>
  Or pass --testcontainers-no-reuse for this run.
  ```

- `ContainerStartError` → echo the maker name, the image tag, and a
  tail of the container logs (last 50 lines via
  `container.logs(tail=50)`).

- `CleanSessionFixtureError` → preserved as-is (NOT remapped to
  `pytest.UsageError`). It is a test-time failure (admin SQL/JS
  failed against a running container), not a setup misconfiguration,
  and the user wants the full `command` + `stderr` + `exit_code` in
  the pytest report. Surfaces with `logger.error(exc_info=...)` plus
  re-raise; pytest reports it like any other in-test exception.

### 9.3 atexit cleanup behavior

The `atexit` safety net:

```python
def _atexit_stop(container):
    try:
        container.stop()
    except Exception:
        # Interpreter shutdown — exceptions are useless. Log to
        # stderr so the failure isn't completely invisible, then
        # continue. Re-raising would mask test failures.
        traceback.print_exc()
```

This is the only `try/except Exception` that suppresses; it has the
mandatory `traceback.print_exc()` per CLAUDE.md error-handling rules.
Every other except block either logs+re-raises or maps to
`UsageError`.

---

## 10. Dependencies

Runtime (in `pyproject.toml`):

```toml
[project]
requires-python = ">=3.10"
dependencies = [
    "pytest >=7.4,<10",
    "testcontainers >=4.7,<5",   # 4.7 ships current wait_strategies API
    "docker >=6.1",              # docker-py for daemon ping +
                                 # reuse-by-name lookup. testcontainers-python
                                 # already depends on it transitively;
                                 # we list explicitly because we use
                                 # docker.errors.NotFound directly.
    "tomli >=2 ; python_version < '3.11'",
]

[project.optional-dependencies]
dev = [
    "pytest-xdist",
    "ruff",
    "pre-commit",
    # Pull in all clean-session fast-path clients so the fast-path branches
    # are exercised in our own test suite. `[clients]` is defined in
    # §4.7 (psycopg + pymysql + pymongo).
    "pytest-testcontainers[clients]",
    # Clients we use directly in test code but which aren't part of
    # any clean-session (`redis-py` for connection-level Redis tests; `pika`
    # for RabbitMQ AMQP-level tests):
    "redis",
    "pika",
]
```

Floors are conservative; no tight ceilings. `testcontainers >=4.7`
because that's where the modern `wait_strategies` module is.

**Note on `docker` (docker-py) floor.** As of testcontainers-python
4.14, the `testcontainers` package itself declares `docker` as a
dependency without any version pin (verified on PyPI 2026-05-08;
`requires_dist` lists `docker` with no specifier). Our floor of
`>=6.1` is a deliberate independent choice for *our* uses
(`docker.errors.NotFound`, container-name lookup, `exec_run(demux=
True)`), not an upstream constraint. We pick 6.1 because it predates
the breaking changes in docker-py 7's connection-pooling defaults
that would force us to ship workarounds. docker-py 7.x is verified
to also work with our usage; the `<8` ceiling is omitted (no tight
ceiling) so users with newer docker-py installations are not blocked.
If a future docker-py major breaks our paths, we'll add a ceiling at
that point.

We do NOT depend on:

- `pytest-django` (lives in package #2).
- The user's preferred client library at runtime (`psycopg`, `redis`,
  …) — users bring their own. The clean-session fixtures (`tc_psql_db` etc.)
  auto-detect a Python client and **fall back to `docker exec`** when
  it isn't installed (full design in §4.6). Clean-session fixtures therefore
  work out of the box with **zero extra dependencies** — installing
  `psycopg[binary]` / `pymysql` / `pymongo` (or the convenience extra
  `pytest-testcontainers[clients]`) is purely an opt-in speedup. No
  fixture raises `UsageError` for missing client libs.

---

## 11. Repo layout

```
pytest-testcontainers/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE                              # MIT
├── .pre-commit-config.yaml              # ruff format + check
├── .github/
│   └── workflows/
│       ├── ci.yml                       # see §12
│       └── publish.yml                  # tag push → PyPI via uv
├── src/
│   └── pytest_testcontainers/
│       ├── __init__.py                  # re-exports makers + DbConnInfo
│       ├── plugin.py                    # entry point: registers fixtures
│       ├── makers.py                    # make_postgres, make_redis, …
│       ├── fixtures.py                  # tc_psql, tc_psql_func, tc_psql_db, …
│       ├── containers.py                # _check_docker_daemon,
│       │                                # _find_existing_container,
│       │                                # _get_host_port, reuse logic,
│       │                                # Ryuk handling, atexit
│       ├── reuse.py                     # project-name + worker-id resolution
│       ├── errors.py                    # exception classes
│       └── _internal/
│           ├── __init__.py
│           ├── docker_health.py
│           ├── port_resolver.py
│           └── conn_info.py             # DbConnInfo dataclass
└── tests/
    ├── conftest.py
    ├── test_makers_postgres.py          # docker-required
    ├── test_makers_redis.py
    ├── test_makers_mysql.py
    ├── test_makers_mongo.py
    ├── test_makers_rabbitmq.py
    ├── test_makers_generic.py           # make_container with custom Cls
    ├── test_fixtures_session.py
    ├── test_fixtures_func.py
    ├── test_fixtures_clean_session_psql.py
    ├── test_fixtures_clean_session_redis.py
    ├── test_fixtures_clean_session_mysql.py
    ├── test_fixtures_clean_session_mongo.py
    ├── test_xdist.py                    # asserts per-worker container count
    ├── test_reuse_mode.py
    ├── test_reuse_collisions.py
    ├── test_docker_not_running.py       # docker mocked-out
    ├── test_disabled.py                 # --no-testcontainers behavior
    └── test_project_name_resolution.py
```

Plugin entry point in `pyproject.toml`:

```toml
[project.entry-points.pytest11]
testcontainers = "pytest_testcontainers.plugin"
```

---

## 12. CI strategy

GitHub Actions, single workflow `ci.yml`.

Matrix:
- OS: `ubuntu-latest` (Docker available natively on hosted runners).
- Python: 3.10, 3.11, 3.12, 3.13.
- Pytest: 7.x and 8.x (separate jobs, install pinned wheel).

Steps:

1. `astral-sh/setup-uv@v5` (mirrors BPP).
2. `uv sync --all-extras --frozen` against a committed `uv.lock`.
3. Pre-commit (`uvx pre-commit run --all-files`), ruff format, ruff
   check.
4. Unit tests (no Docker required — daemon-ping mocked).
   `_docker_required` is registered in `pyproject.toml` as a custom
   pytest marker (`[tool.pytest.ini_options] markers = ...`); tests
   that need a real daemon decorate with
   `@pytest.mark.docker_required`. Filter by marker (`-m`), not by
   name (`-k`):
   `uv run pytest tests/ -m "not docker_required"`.
5. Integration tests (Docker required, all `docker_required`-marked
   tests plus all of `tests/test_makers_*.py`):
   `uv run pytest tests/`.
6. Reuse smoke: same suite twice in a row with
   `PYTEST_TESTCONTAINERS_REUSE=1`, asserting on the second run that
   no new container was created (read `Created` timestamps via
   docker-py).
7. xdist smoke: `uv run pytest -n 4 tests/test_xdist.py`, assert
   exactly `-n` containers were created (per-worker session-scope
   semantics).

macOS / Windows runners deliberately excluded from CI (Docker setup
on hosted runners for those platforms is brittle and slow). Users on
those platforms run their own; we don't promise CI parity.

Release: tag push (`v*`) triggers `publish.yml` → `uv build` →
`uv publish` with PyPI Trusted Publishing (no API token in repo).

---

## 13. README structure (mandatory sections)

When the README gets implemented (out of scope for this spec but
documented here so the implementer doesn't omit anything):

1. **Quickstart (5 lines using `tc_psql`).** Trivial copy-paste:

   ```python
   def test_my_thing(tc_psql):
       host = tc_psql.get_container_host_ip()
       port = int(tc_psql.get_exposed_port(5432))
       # …connect with your library of choice
   ```

   Not a tutorial. Just enough that a user goes "oh, that's it" and
   reads further.

2. **The full table of services + variants.** The §4.5 table verbatim.

3. **Image override examples.** The full §5 code block verbatim
   (Postgres 18 / pinned minor / custom image / custom env / custom
   credentials). Each as its own copy-pastable snippet.

4. **"Container scope: when to use what".** The §6 ladder with the
   480× math, the concrete numbers ("100 tests × 5s = 8 minutes"),
   and the three legitimate function-scoped use cases.

5. **"Surprising thing #1: xdist session scope".** The §7.1/§7.2
   worked example. Phrased as "if you only read one section, read
   this one".

6. **Clean-session pattern walkthrough.** Step-by-step for `tc_psql_db`:
   what a fresh DB per test means, what the underlying SQL does, how
   to write your own variant on top of a custom-image session
   container (the "combined with clean-session pattern" §5 snippet).

7. **Custom services via `make_container`.** Show wiring a Kafka
   container with `make_container(KafkaContainer, …)` so users see
   how to extend without our shipping the maker.

8. **Reuse mode.** What it is, when to use it (`--testcontainers-reuse`
   for fast iterative dev loops), how to clean up
   (`--testcontainers-clean`), and the "what if I have two pytest
   invocations running at once" caveat (each must use its own
   `PYTEST_TESTCONTAINERS_PROJECT`).

9. **Error scenarios.** Sample messages from §9.2, especially the
   Docker-not-running one.

10. **Comparison.** A short "vs. pytest-docker-compose / vs.
    hand-rolled fixtures / vs. testcontainers-python directly" table
    so users can self-select.

The README MUST NOT show the eager-start machinery or
`pytest_load_initial_conftests` (it's not in this package).
References to "if you need env-injection before Django settings
import, see `pytest-testcontainers-django`" are fine and expected.

---

## 14. Generalization checklist (post-refactor)

Mapping every BPP-specific thing in `testcontainers_bpp/containers.py`
to its new home. Most things move to #2 or #3 because the eager-start
machinery is no longer in #1.

| Current (BPP) | New home | Notes |
|---|---|---|
| `BPP_USE_TESTCONTAINERS=0` env var | #1: `PYTEST_TESTCONTAINERS=0` | Same semantics, generic name. |
| `--no-testcontainers` CLI flag | #1: same flag, kept verbatim | Familiar UX. |
| `BPP_TESTCONTAINERS_REUSE=1` env | #1: `PYTEST_TESTCONTAINERS_REUSE=1` | |
| Hard-coded PG image `iplweb/bpp_dbserver:psql-16.13` | User-side: `make_postgres(image=…)` in their fixture | Default in #1 is `postgres:16`. |
| Hard-coded user `bpp` / pass `password` / db `bpp` | User-side: `make_postgres(username=…, password=…, database=…)` | Default in #1 is `test`/`test`/`test`. |
| `_PG_NAME = "bpp-tc-pg"` / `_REDIS_NAME = "bpp-tc-redis"` | #1: templated name, default `{project}-tc-{service}-{branch}-{dirtag}-{worker}` (§8.7) | Project name resolved from `pyproject.toml [project].name`. |
| `os.environ["DJANGO_BPP_DB_HOST"] = …` (5 vars) | #2 (`pytest-testcontainers-django`) | This was the eager-start env-injection. Out of #1 entirely. |
| `os.environ["DJANGO_BPP_TEST_TEMPLATE"] = "bpp"` | #3 (`django-pg-baseline`) | TEMPLATE wiring is Django-creation-machinery layer. |
| `os.environ["DJANGO_BPP_SKIP_DOTENV"] = "1"` | #2 | Suppressing `.env` re-read after env injection — Django-specific. |
| `find_baseline_sql()` + mount to `/docker-entrypoint-initdb.d/` | #3 | baseline.sql discovery + volume wiring. |
| `POSTGRESQL_UNSAFE_BUT_FAST=1`, `POSTGRESQL_MAX_LOCKS_PER_TRANSACTION=512` | User-side: `make_postgres(env={…})` | Image-specific tuning; not generic. |
| `com.docker.compose.*` label scrubbing | User-side, if needed: `**kwargs` pass-through to `with_kwargs(labels={…})` | We don't scrub by default. |
| `print("[testcontainers-bpp] …")` | #1: `print("[pytest-testcontainers] …")` | Branding update. |
| `BppContainers` dataclass with `pg`/`redis`/`pg_host`/`pg_port`/… | #1: maker returns the testcontainers-python instance directly | No custom wrapper in any mode (fresh-start, restart-stopped, or bind-to-running-reuse all yield the same upstream class instance — see §4.1.2 step 6). User code calls `.get_container_host_ip()` / `.get_exposed_port()`. |
| `RuntimeError("testcontainers is required …")` import-time guard | #1: same conceptually; message points at `pip install pytest-testcontainers[dev]` | |

Items kept verbatim (already generic):
- `_check_docker_daemon` (`docker.from_env().ping()` health check).
- `_find_existing_container` semantics (return None / restart / raise
  on failure-to-start).
- `_get_host_port` (extract from `NetworkSettings.Ports`, normalize
  `""`/`0.0.0.0` → `localhost`; per open question, ALSO normalize
  `::`).
- `atexit` safety net (only fires if `pytest_unconfigure` was
  skipped).
- Ryuk-disabled-when-reuse pattern.

---

## 15. Boundaries with #1, #2, #3

| Feature | #1 `pytest-testcontainers` | #2 `pytest-testcontainers-django` | #3 `django-pg-baseline` |
|---|---|---|---|
| `make_postgres` / `make_redis` / … | ✅ owns | uses | uses |
| `tc_psql` / `tc_redis` / etc. session fixtures | ✅ owns | uses | uses |
| `tc_psql_db` / `tc_psql_func` / clean-session fixtures | ✅ owns | uses | uses |
| Reuse mode (Ryuk disabled, named containers, restart-stopped) | ✅ owns | inherits | inherits |
| `_check_docker_daemon`, port resolution, atexit cleanup | ✅ owns | inherits | inherits |
| `pytest_load_initial_conftests` eager hook | ❌ NOT here | ✅ owns | uses |
| `os.environ["DJANGO_…_DB_HOST"] = …` env injection | ❌ NOT here | ✅ owns | — |
| Suppressing `.env` re-read after injection | ❌ NOT here | ✅ owns | — |
| `pytest-django` plugin-order coordination (`tryfirst=True`) | ❌ NOT here | ✅ owns | — |
| `DATABASES['default']['TEST']['TEMPLATE']` setup | ❌ NOT here | uses | ✅ owns |
| `baseline.sql` discovery (`$BPP_BASELINE_SQL_PATH` + convention) | ❌ NOT here | — | ✅ owns |
| Mounting `baseline.sql` into PG init scripts | ❌ NOT here | — | ✅ owns (wires via `make_postgres(volumes=…)`) |
| `_execute_create_test_db` monkeypatch fallback | ❌ NOT here | — | ✅ owns |
| BPP-specific names (`DJANGO_BPP_*`, `iplweb/bpp_dbserver`) | ❌ never | ❌ never (generic env names) | ❌ never |

#2's eager-start machinery uses #1's `make_postgres` / `make_redis` —
it constructs the maker context manager INSIDE the
`pytest_load_initial_conftests` hook, enters it, and arranges the
host/port to be visible in `os.environ` before `pytest-django`
imports the user's settings. The "this maker is being used eagerly
vs. lazily" distinction is invisible to #1 — it just hands back a
context manager.

**Import-time guarantee for #2.** `pytest_testcontainers` is
import-safe at any point in the pytest lifecycle, including
**before** `pytest_configure` and from inside
`pytest_load_initial_conftests` (which runs earlier than any user
conftest). Specifically:

- Importing `pytest_testcontainers` does NOT call
  `docker.from_env()`, ping the daemon, read env vars, parse
  `pyproject.toml`, or touch `os.environ`.
- It does NOT spawn Ryuk, register `atexit` callbacks, or instantiate
  any testcontainers-python class.
- Side-effecting code paths (daemon ping, Ryuk-disable, project-name
  resolution, atexit registration) all live inside maker
  `__enter__`, not at module top level.

This is what makes #2's eager-start hook viable: it can
`from pytest_testcontainers import make_postgres` at the top of its
plugin module and call `make_postgres(...)` inside
`pytest_load_initial_conftests` without triggering any of #1's
runtime side effects until #2 explicitly enters the context manager.
We intend to keep this property indefinitely; it's part of #1's
public contract with #2 and #3.

---

## 16. Open questions

Re-evaluated after the refactor. Several previous open questions are
moot now (TOML schema strictness, `kind` registry,
`pytest_testcontainers_configure` discovery, `enabled = false`
fixture exposure) because none of those mechanisms exist in #1
anymore.

1. **Clean-session fixture client library import strategy.**
   **RESOLVED (user, 2026-05-08, revised): auto-detect Python client,
   fallback to `docker exec` with one-shot warning.** Full design
   in §4.6.

   Summary of what was decided:
   - **Default install (no extras): clean-session fixtures work via `docker exec`**,
     using the in-container CLI tools (`psql`, `mysql`, `mongosh`,
     `redis-cli`). Zero host-side deps. Tests run out of the box.
   - **If client lib is importable** (`psycopg` / `pymysql` /
     `pymongo`), the fixture automatically uses it. ~20× faster per
     admin call (~5ms vs ~100ms).
   - **One-shot stderr warning** at first clean-session invocation when
     fallback is in use, hinting at the install command. Suppress
     with `PYTEST_TESTCONTAINERS_QUIET=1`.
   - **Optional extras** (`clients-postgres`, `clients-mysql`,
     `clients-mongo`, aggregate `clients`) are convenience for users
     who want fast paths in one install line, not a hard requirement.
   - **Redis is exec-only** (no auto-detect): `redis-cli FLUSHALL`
     via `container.exec(...)` is faster than the Python client for
     this single-shot use case, see §4.6 for cost analysis.
   - **No `clean_session_strategy` config flag** in v1 — auto by import is
     deterministic and debuggable.

   Earlier resolution (lazy import + deferred `pytest.UsageError`)
   superseded by the docker-exec fallback approach: zero-deps
   adoption beats deferred-error friction.

2. **Postgres maker default version: `postgres:16` or `postgres`?**
   Pinning to `:16` gives reproducible defaults (Postgres 17/18
   coming through automatic image updates would surprise users).
   `postgres` (no tag = `:latest`) is friendlier for "I just want a
   PG, any PG" cases. **Proposal: `postgres:16` — pinning matches
   testcontainers-python's own examples and is more honest about
   "this is what's tested in our CI."** Bumped on each major release
   of pytest-testcontainers (so v1.0 = postgres:16, v1.5 might =
   postgres:18).

3. **`tc_psql_db` connection-info shape vs. live connection.** Yield
   a `DbConnInfo` (host/port/user/pass/dbname) and let the user
   `psycopg.connect(**asdict(info))`, OR yield a live
   `psycopg.Connection`?

   - Connection-info: more flexible (works with any client lib,
     ORM, raw URL); user has to write one extra line.
   - Live connection: zero-friction; couples us to one client lib;
     "what about `asyncpg`?" never has a clean answer.

   **Proposal: `DbConnInfo`.** The fixture is about isolation, not
   about "give me a connection." Same logic for MySQL and Mongo
   clean-session fixtures.

4. **macOS port mapping IPv6 edge case.** On macOS with Docker
   Desktop, `NetworkSettings.Ports[*][0].HostIp` sometimes returns
   `0.0.0.0`, sometimes `::`. BPP's `_get_host_port` only normalizes
   `""` and `0.0.0.0`. **Proposal: also normalize `::` → `localhost`.
   Confirmed for v1.**

5. **`make_container(Cls, ...)` and reuse names for unknown classes.**
   For ship-ed makers, the service name in the reuse string is hard-
   coded (`psql`, `redis`, …). For `make_container(KafkaContainer)`,
   we don't have one. Options:
   - Derive from `Cls.__name__.lower().removesuffix("container")` →
     `kafka`. Implicit; can collide if user has two Kafka makers
     with different images.
   - Require user to pass `reuse_name=` explicitly when reuse mode is
     on; raise `UsageError` if absent. More boilerplate; explicit.

   **Proposal: derive from `Cls.__name__` by default, allow override
   via `reuse_name=`.** Documented as "if you have two
   `make_container(SameClass)` fixtures with different images, give
   them distinct `reuse_name=`."

6. **`tc_redis_clean` `FLUSHDB` vs `FLUSHALL`.** Redis clients
   typically default to db 0; multi-db is rare in app code but real
   in test code. `FLUSHDB` cleans only db 0; `FLUSHALL` cleans
   everything. **RESOLVED (user, 2026-05-08): `FLUSHALL`.** Update
   §4.3 / §4.5 / §4 references accordingly — leftover keys in
   non-zero DBs after previous test would be a surprising footgun,
   no real cost difference.

7. **Project-name fallback when no `pyproject.toml [project].name`
   exists.** **RESOLVED (user, 2026-05-08): literal `"pytest-tc"`
   fallback + one-line stderr warning at first maker invocation
   when fallback is used: "Tip: set `PYTEST_TESTCONTAINERS_PROJECT=…`
   to avoid collisions with other projects on the same machine."**
   Hash-of-cwd alternative rejected as too implicit.

8. **Polish vs. English.** BPP code is bilingual; OSS audience is
   global. **Proposal: English-only for v1.** All log/error messages.

9. **Should `make_container(Cls, ...)` daemon-ping like the named
   makers?** Yes — same code path, same maker layer. Documented as
   "every maker pings; the ping is one-shot per process".

10. **Behavior when same fixture is requested by multiple tests
    inside one xdist worker session.** Standard pytest fixture
    caching applies; the maker `__enter__` runs once per worker.
    Mentioned for clarity; nothing to decide.

---

## 17. What this package does NOT do — pointer to siblings

(Restated for emphasis after the refactor; #16 covers boundaries
fully.)

#1 ships fixtures and makers. That is the whole package. It does not
ship:

- **Eager-start of containers before user `conftest.py` /
  `settings.py` imports.** That's #2.
- **Env-var injection of host/port into `os.environ`.** That's #2.
- **`.env` re-read suppression.** That's #2.
- **`pytest-django` plugin-order coordination.** That's #2.
- **Postgres `WITH TEMPLATE` setup for fast Django test-db creation.**
  That's #3.
- **`baseline.sql` discovery and mounting into PG init scripts.**
  That's #3.
- **The CI fallback that does `psql … < baseline.sql` after `CREATE
  DATABASE`.** That's #3.

A user who wants the BPP-style "Django settings module reads
`os.environ` at import, must see container host/port already injected
by then" experience installs `pytest-testcontainers-django` (#2),
which installs #1 as a dependency and adds the eager-start machinery
on top. The maker functions are the same (#1's `make_postgres` is
called from inside #2's eager-start hook).

A user who just wants "give me a Postgres fixture for my pytest
tests" installs only #1.

---

## 18. Differentiation vs. the existing landscape

Quick reality check on "is this duplicating something that exists?":

- **`testcontainers-python`** itself ships NO pytest fixtures and NO
  pytest entry point. Confirmed via PyPI page + project README
  inspection. Users who want fixtures hand-roll them. We add the
  fixture layer, the maker convention, and the
  function-scoped/clean-session variants.

- **`pytest-docker-compose`** drives a `docker-compose.yml`. Different
  abstraction (compose) and different lifecycle model (per-test
  bring-up by default). Not a competitor; complementary.

- **`pytest-docker`** (the avast one) is similar to
  `pytest-docker-compose` — compose-driven, not testcontainers-driven.

- **`pytest-container`** (`pytest_container`) is for testing container
  IMAGES (testinfra-style), not for spinning up dependencies during
  tests of OTHER code. Different use case.

- **`tomodachi-testcontainers`** is a framework-specific bundle for
  the Tomodachi framework. Useful as prior art for the maker pattern;
  not a generic alternative.

Conclusion: the niche "named pytest fixtures + maker pattern + clean-session
fresh-state fixtures on top of testcontainers-python" is unoccupied.
The plugin's value is the boring stuff: shipping the ~150 lines every
project reinvents, plus the educational scope-choice ladder (§6) that
nudges users away from per-test-container anti-patterns.
