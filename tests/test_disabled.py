"""``--no-testcontainers`` / ``PYTEST_TESTCONTAINERS=0`` makes fixtures raise."""

from __future__ import annotations

import pytest

from pytest_testcontainers.fixtures import _check_enabled
from pytest_testcontainers.reuse import set_cli_disabled


def test_check_enabled_raises_when_cli_disabled() -> None:
    set_cli_disabled(True)
    with pytest.raises(pytest.UsageError) as excinfo:
        _check_enabled()
    assert "disabled via" in str(excinfo.value)


def test_check_enabled_raises_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS", "0")
    with pytest.raises(pytest.UsageError):
        _check_enabled()


def test_check_enabled_passes_by_default() -> None:
    _check_enabled()  # must not raise


@pytest.mark.parametrize("value", ["false", "no", "off", "FALSE"])
def test_disabled_env_truthy_variants(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PYTEST_TESTCONTAINERS", value)
    with pytest.raises(pytest.UsageError):
        _check_enabled()
