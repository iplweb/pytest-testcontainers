"""``DbConnInfo`` — connection-info dataclass yielded by clean-session DB fixtures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DbConnInfo:
    """Frozen bundle of connection coordinates for a clean-session DB fixture.

    Yielded by ``tc_psql_db`` / ``tc_mysql_db`` / ``tc_mongo_db``. The
    fixture is about isolation, not about handing back a live client —
    user code constructs its preferred client/ORM/URL from these fields.
    """

    host: str
    port: int
    username: str
    password: str
    database: str

    def url(self, scheme: str = "postgresql") -> str:
        """Render a DB URL of the form ``<scheme>://user:pass@host:port/db``.

        ``scheme`` defaults to ``"postgresql"``. Pass ``"mysql"``,
        ``"mongodb"``, or any other scheme as needed — no validation.
        """
        return f"{scheme}://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"
