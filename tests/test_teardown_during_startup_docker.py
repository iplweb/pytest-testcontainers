"""Killing a run *while a container is still booting* must not leak it.

The window between "Docker created the container" and "`.start()` returned"
is the wait strategy — seconds for a Postgres that has to replay an init
script. It is also the window a developer is most likely to abort in,
because that is when the machine feels stuck.

The parent here does not wait for the child to report readiness; it polls
Docker directly and signals the moment the container appears, which lands
inside that window. Ryuk runs with a 120 s reconnection timeout, so a pass
cannot be its doing.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = pytest.mark.docker_required

_CHILD = textwrap.dedent(
    """
    import time
    from pytest_testcontainers import make_postgres

    with make_postgres() as container:
        print(container._container.name, flush=True)
        time.sleep(120)
    """
)

_NAME_FRAGMENT = "-tc-psql-"
_APPEAR_BUDGET_S = 60.0
_TEARDOWN_BUDGET_S = 30.0


def _docker():  # type: ignore[no-untyped-def]
    import docker

    return docker.from_env()


def _find_starting_container(client, since: float) -> str | None:  # type: ignore[no-untyped-def]
    for container in client.containers.list(all=True):
        name = container.name or ""
        if _NAME_FRAGMENT in name and container.attrs["Created"] >= since:
            return name
    return None


def _exists(client, name: str) -> bool:  # type: ignore[no-untyped-def]
    from docker.errors import NotFound

    try:
        client.containers.get(name)
    except NotFound:
        return False
    return True


def test_sigterm_during_container_startup_removes_it() -> None:
    client = _docker()
    env = dict(os.environ)
    env["RYUK_RECONNECTION_TIMEOUT"] = "120s"
    env["PYTEST_TESTCONTAINERS_QUIET"] = "1"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 5))

    with subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    ) as child:
        try:
            deadline = time.monotonic() + _APPEAR_BUDGET_S
            name = None
            while time.monotonic() < deadline and child.poll() is None:
                name = _find_starting_container(client, started_at)
                if name:
                    break
                time.sleep(0.05)
            assert name, "container never appeared in Docker"

            # Deliberately no wait for the child to print — it only does that
            # once .start() has returned, which is the window we must skip.
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=_TEARDOWN_BUDGET_S)

            deadline = time.monotonic() + _TEARDOWN_BUDGET_S
            while time.monotonic() < deadline:
                if not _exists(client, name):
                    return
                time.sleep(0.25)
            pytest.fail(
                f"container {name!r} survived SIGTERM sent during startup; "
                "teardown was armed too late and fell through to Ryuk"
            )
        finally:
            if child.poll() is None:
                child.kill()
