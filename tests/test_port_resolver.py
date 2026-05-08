from __future__ import annotations

import pytest

from pytest_testcontainers._internal.port_resolver import normalize_host


@pytest.mark.parametrize(
    "host,expected",
    [
        ("", "localhost"),
        ("0.0.0.0", "localhost"),
        ("::", "localhost"),
        ("::0", "localhost"),
        (None, "localhost"),
        ("172.17.0.2", "172.17.0.2"),
        ("docker.internal", "docker.internal"),
    ],
)
def test_normalize_host(host: str | None, expected: str) -> None:
    assert normalize_host(host) == expected
