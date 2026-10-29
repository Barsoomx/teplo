from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.write_guard import write_allowed

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ('last_command_at', 'expected'),
    [
        (None, True),
        (NOW - timedelta(minutes=29), False),
        (NOW - timedelta(minutes=30), True),
    ],
)
def test_write_allowed_respects_thirty_minutes(last_command_at: datetime | None, expected: bool) -> None:
    assert write_allowed(last_command_at, NOW) is expected


def test_write_allowed_when_clock_moved_back_past_last_command() -> None:
    assert write_allowed(NOW + timedelta(hours=1), NOW) is True
