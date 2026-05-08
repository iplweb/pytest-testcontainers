"""Host/port resolution that normalizes Docker's quirks.

Docker's ``NetworkSettings.Ports`` returns ``HostIp`` strings that vary
across platforms: ``""`` and ``"0.0.0.0"`` on Linux, ``"::"`` on macOS
with Docker Desktop. We normalize all of those to ``localhost`` so test
code does not have to know about the platform difference.
"""

from __future__ import annotations

_LOOPBACK_ALIASES = {"", "0.0.0.0", "::", "::0"}


def normalize_host(host: str | None) -> str:
    """Map Docker's ``HostIp`` to a usable hostname for connecting back."""
    if host is None:
        return "localhost"
    if host in _LOOPBACK_ALIASES:
        return "localhost"
    return host
