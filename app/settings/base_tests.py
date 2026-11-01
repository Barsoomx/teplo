import json

import pytest
import structlog
from structlog.testing import CapturingLogger


@pytest.fixture
def f_logger() -> CapturingLogger:
    return CapturingLogger()


def test_logged_traceback_leaves_out_local_variables(f_logger: CapturingLogger) -> None:
    local_key = 'tuya-local-key'
    try:
        raise ValueError(f'a local key of {len(local_key)} characters was refused')
    except ValueError:
        structlog.wrap_logger(f_logger).exception('device_refused_key')

    record = json.loads(f_logger.calls[0].args[0])
    frames = record['exception'][0]['frames']
    assert (len(frames), [frame for frame in frames if 'locals' in frame], local_key in f_logger.calls[0].args[0]) == (
        1,
        [],
        False,
    )
