"""End-to-end proof that SIGTERM tears containers down without Ryuk.

The child process runs with ``RYUK_RECONNECTION_TIMEOUT=120s``, so if the
container disappears at all it can only be our own signal handler doing
it — Ryuk could not possibly have reacted yet. Before the handler existed
this test failed by timeout: the container outlived the killed process
and was reaped ~10 s later by Ryuk.
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
    import sys, time
    from pytest_testcontainers import make_redis

    with make_redis() as container:
        print(container._container.name, flush=True)
        time.sleep(120)
    """
)

# Long enough that our handler has clearly finished, far below the child's
# Ryuk reconnection timeout so a pass cannot be Ryuk's doing.
_TEARDOWN_BUDGET_S = 20.0


def _container_exists(name: str) -> bool:
    import docker
    from docker.errors import NotFound

    try:
        docker.from_env().containers.get(name)
    except NotFound:
        return False
    return True


def _wait_gone(name: str, budget: float) -> float:
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if not _container_exists(name):
            return time.monotonic() - (deadline - budget)
        time.sleep(0.25)
    return float("inf")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_signal_removes_container_before_ryuk_could(signum: int) -> None:
    env = dict(os.environ)
    env["RYUK_RECONNECTION_TIMEOUT"] = "120s"
    env["PYTEST_TESTCONTAINERS_QUIET"] = "1"

    with subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    ) as child:
        try:
            assert child.stdout is not None
            assert child.stderr is not None
            name = child.stdout.readline().strip()
            assert name, f"child never reported a container name: {child.stderr.read()!r}"
            assert _container_exists(name)

            child.send_signal(signum)
            child.wait(timeout=_TEARDOWN_BUDGET_S)

            elapsed = _wait_gone(name, _TEARDOWN_BUDGET_S)
            survived = f"container {name!r} survived {signum!r}; teardown fell through to Ryuk"
            assert elapsed != float("inf"), survived
        finally:
            if child.poll() is None:
                child.kill()
