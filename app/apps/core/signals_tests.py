import pytest

from apps.core.signals import SQLITE_PRAGMAS, configure_sqlite


class CursorStub:
    def __init__(self) -> None:
        self._statements: list[str] = []

    @property
    def statements(self) -> list[str]:
        return self._statements

    def execute(self, sql: str) -> object:
        self._statements.append(sql)
        return None

    def close(self) -> None:
        return None


class ConnectionStub:
    def __init__(self, vendor: str, cursor: CursorStub) -> None:
        self._vendor = vendor
        self._cursor = cursor

    @property
    def vendor(self) -> str:
        return self._vendor

    def cursor(self) -> CursorStub:
        return self._cursor


@pytest.fixture
def f_cursor() -> CursorStub:
    return CursorStub()


@pytest.fixture
def f_sqlite_connection(f_cursor: CursorStub) -> ConnectionStub:
    return ConnectionStub(vendor='sqlite', cursor=f_cursor)


@pytest.fixture
def f_postgres_connection(f_cursor: CursorStub) -> ConnectionStub:
    return ConnectionStub(vendor='postgresql', cursor=f_cursor)


def test_sqlite_connection_gets_pragmas(f_sqlite_connection: ConnectionStub, f_cursor: CursorStub) -> None:
    configure_sqlite(connection=f_sqlite_connection)

    assert f_cursor.statements == list(SQLITE_PRAGMAS)


def test_other_vendors_are_left_alone(f_postgres_connection: ConnectionStub, f_cursor: CursorStub) -> None:
    configure_sqlite(connection=f_postgres_connection)

    assert f_cursor.statements == []
