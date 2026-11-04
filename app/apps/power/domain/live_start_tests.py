import math

import pytest

from apps.power.domain.live_start import PENDING_START_LIMIT, LiveStart

AT = 1000.0
PERMITTED = LiveStart(permitted_at=AT, sent_at=None)


@pytest.mark.parametrize(
    ('start', 'at', 'counted'),
    [
        (PERMITTED, AT + 25, math.inf),
        (PERMITTED, AT + PENDING_START_LIMIT, None),
        (LiveStart(permitted_at=AT, sent_at=AT + 12), AT + 300, AT + 12),
    ],
    ids=['permitted_and_not_sent', 'permit_never_used', 'sent'],
)
def test_live_start_counts_until_a_reading_after_its_command(
    start: LiveStart,
    at: float,
    counted: float | None,
) -> None:
    assert start.counted_until_read_after(at) == counted


@pytest.mark.parametrize(
    ('before', 'at', 'after'),
    [
        (PERMITTED, AT + 12, LiveStart(permitted_at=AT, sent_at=AT + 12)),
        (PERMITTED, AT - 1, PERMITTED),
        (LiveStart(permitted_at=AT, sent_at=AT + 12), AT + 5, LiveStart(permitted_at=AT, sent_at=AT + 12)),
        (LiveStart(permitted_at=AT, sent_at=AT + 12), AT + 20, LiveStart(permitted_at=AT, sent_at=AT + 20)),
    ],
    ids=['after_its_permit', 'never_before_its_permit', 'never_before_its_last_command', 'after_its_last_command'],
)
def test_live_start_is_sent_no_sooner_than_its_permit_or_its_last_command(
    before: LiveStart,
    at: float,
    after: LiveStart,
) -> None:
    assert before.sent_by(at) == after


def test_command_sent_without_a_permit_counts_from_its_moment() -> None:
    assert LiveStart.sent(AT) == LiveStart(permitted_at=AT, sent_at=AT)
