import uuid
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from apps.system.services.boot_id import BootIdReader

PROC_BOOT_ID_CONTENT = b'5D1C2A7E-0B4F-4C8A-9E3D-6F2B1A0C9D8E\n'
BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
ONE_WARNING = [('router_boot_id_unreadable', 'warning')]


@pytest.fixture
def f_reader(tmp_path: Path) -> BootIdReader:
    path = tmp_path / 'boot_id'
    path.write_bytes(PROC_BOOT_ID_CONTENT)
    return BootIdReader(path=path)


@pytest.fixture
def f_missing_reader(tmp_path: Path) -> BootIdReader:
    return BootIdReader(path=tmp_path / 'boot_id')


@pytest.fixture
def f_garbled_reader(tmp_path: Path, request: pytest.FixtureRequest) -> BootIdReader:
    path = tmp_path / 'boot_id'
    path.write_bytes(request.param)
    return BootIdReader(path=path)


def test_boot_id_is_the_uuid_the_kernel_gives_in_canonical_form(f_reader: BootIdReader) -> None:
    with capture_logs() as logs:
        boot_id = f_reader.read()

    assert (boot_id, logs) == (BOOT_ID, [])


def test_missing_file_gives_no_boot_id_and_one_warning(f_missing_reader: BootIdReader) -> None:
    with capture_logs() as logs:
        boot_id = f_missing_reader.read()

    assert (boot_id, [(log['event'], log['log_level']) for log in logs]) == (None, ONE_WARNING)


@pytest.mark.parametrize(
    'f_garbled_reader',
    [b'', b'\n', b'boot 7\n', b'5d1c2a7e-0b4f-4c8a-9e3d\n', b'5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e7f\n', b'\xff\xfe\n'],
    ids=['empty', 'blank_line', 'words', 'too_short', 'too_long', 'not_text'],
    indirect=True,
)
def test_garbled_file_gives_no_boot_id_and_one_warning(f_garbled_reader: BootIdReader) -> None:
    with capture_logs() as logs:
        boot_id = f_garbled_reader.read()

    assert (boot_id, [(log['event'], log['log_level']) for log in logs]) == (None, ONE_WARNING)


def test_reader_reads_the_kernel_boot_id() -> None:
    boot_id = BootIdReader.init().read()

    assert boot_id is not None and str(uuid.UUID(boot_id)) == boot_id
