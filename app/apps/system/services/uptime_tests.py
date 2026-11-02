from datetime import timedelta
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from apps.system.services.uptime import UptimeReader

PROC_UPTIME_CONTENT = b'512.37 1830.12\n'
ONE_WARNING = [('router_uptime_unreadable', 'warning')]


@pytest.fixture
def f_reader(tmp_path: Path) -> UptimeReader:
    path = tmp_path / 'uptime'
    path.write_bytes(PROC_UPTIME_CONTENT)
    return UptimeReader(path=path)


@pytest.fixture
def f_missing_reader(tmp_path: Path) -> UptimeReader:
    return UptimeReader(path=tmp_path / 'uptime')


@pytest.fixture
def f_garbled_reader(tmp_path: Path, request: pytest.FixtureRequest) -> UptimeReader:
    path = tmp_path / 'uptime'
    path.write_bytes(request.param)
    return UptimeReader(path=path)


def test_router_uptime_is_the_first_number_of_proc_uptime(f_reader: UptimeReader) -> None:
    with capture_logs() as logs:
        uptime = f_reader.read()

    assert (uptime, logs) == (timedelta(seconds=512.37), [])


def test_missing_file_gives_no_uptime_and_one_warning(f_missing_reader: UptimeReader) -> None:
    with capture_logs() as logs:
        uptime = f_missing_reader.read()

    assert (uptime, [(log['event'], log['log_level']) for log in logs]) == (None, ONE_WARNING)


@pytest.mark.parametrize(
    'f_garbled_reader',
    [b'', b'up for ages\n', b'nan 1830.12\n', b'inf 1830.12\n', b'\xff\xfe\n'],
    ids=['empty', 'words', 'not_a_number', 'infinite', 'not_text'],
    indirect=True,
)
def test_garbled_file_gives_no_uptime_and_one_warning(f_garbled_reader: UptimeReader) -> None:
    with capture_logs() as logs:
        uptime = f_garbled_reader.read()

    assert (uptime, [(log['event'], log['log_level']) for log in logs]) == (None, ONE_WARNING)


def test_reader_reads_the_kernel_uptime() -> None:
    assert isinstance(UptimeReader.init().read(), timedelta)
