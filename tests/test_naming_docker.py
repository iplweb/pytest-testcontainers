"""docker_required: a default-mode container really carries the templated name,
and reuse identity differs across git branches."""

from __future__ import annotations

import pytest

from pytest_testcontainers import make_redis
from pytest_testcontainers.reuse import container_name_for

pytestmark = pytest.mark.docker_required


def _remove_named(name: str) -> None:
    import docker
    from docker.errors import NotFound

    client = docker.from_env()
    try:
        client.containers.get(name).remove(force=True)
    except NotFound:
        return


def test_default_mode_container_has_templated_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "ptc-name-smoke")
    with make_redis() as r:
        docker_name = r._container.name
        assert docker_name.startswith("ptc-name-smoke-tc-redis-")


def test_reuse_identity_differs_across_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The {branch} component is part of the reuse identity, so two branches
    # resolve to two distinct container names.
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_PROJECT", "ptc-branch-smoke")
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_NAME_TEMPLATE", "{project}-tc-{service}-{branch}")
    monkeypatch.setenv("PYTEST_TESTCONTAINERS_REUSE", "1")

    monkeypatch.setattr(
        "pytest_testcontainers.reuse.git_identity.current_branch",
        lambda start: "branch-a",
    )
    name_a = container_name_for("redis")

    monkeypatch.setattr(
        "pytest_testcontainers.reuse.git_identity.current_branch",
        lambda start: "branch-b",
    )
    name_b = container_name_for("redis")

    assert name_a != name_b
    assert name_a == "ptc-branch-smoke-tc-redis-branch-a"
    assert name_b == "ptc-branch-smoke-tc-redis-branch-b"

    _remove_named("ptc-name-smoke-tc-redis")  # defensive: no leak from sibling test
