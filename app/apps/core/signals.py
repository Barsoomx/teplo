from typing import Protocol

from django.db.backends.signals import connection_created
from django.dispatch import receiver

SQLITE_PRAGMAS = ('PRAGMA journal_mode=WAL', 'PRAGMA synchronous=NORMAL')


class SqlCursor(Protocol):
    def execute(self, sql: str) -> object: ...

    def close(self) -> None: ...


class SqlConnection(Protocol):
    @property
    def vendor(self) -> str: ...

    def cursor(self) -> SqlCursor: ...


@receiver(connection_created)
def configure_sqlite(connection: SqlConnection, **_kwargs: object) -> None:
    if connection.vendor != 'sqlite':
        return

    cursor = connection.cursor()
    for pragma in SQLITE_PRAGMAS:
        cursor.execute(pragma)
    cursor.close()
