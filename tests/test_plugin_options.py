"""Plugin CLI option wiring (no pytester — exercise the hooks directly)."""

from __future__ import annotations

from pytest_testcontainers import plugin, reuse


class _StubConfig:
    """Minimal duck-typed pytest.Config exposing getoption()."""

    def __init__(self, options: dict[str, object]) -> None:
        self._options = options

    def getoption(self, name: str):
        return self._options.get(name)


def _base_options(**overrides: object) -> dict[str, object]:
    opts: dict[str, object] = {
        "testcontainers_disabled": False,
        "testcontainers_project": None,
        "testcontainers_no_reuse": False,
        "testcontainers_reuse": False,
        "testcontainers_name_template": None,
    }
    opts.update(overrides)
    return opts


def test_name_template_flag_wires_into_reuse_state() -> None:
    config = _StubConfig(_base_options(testcontainers_name_template="{service}-from-cli"))
    plugin.pytest_configure(config)
    assert reuse.name_template() == "{service}-from-cli"


def test_no_name_template_flag_leaves_default(tmp_cwd) -> None:
    config = _StubConfig(_base_options())
    plugin.pytest_configure(config)
    assert reuse.name_template() == reuse.DEFAULT_NAME_TEMPLATE
