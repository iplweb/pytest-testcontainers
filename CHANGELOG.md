# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
