from datetime import UTC, datetime, timedelta

import pytest

from apps.telemetry.domain.starts import BootOpening, TeploStart, boot_opening

BOOT = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
EARLIER_BOOT = '0b7d3c1e-9a2f-4e6b-8c5d-3f1a2b4c6d8e'
CUT_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
OPENING = TeploStart(boot_id=BOOT, last_activity=CUT_AT, router_uptime=timedelta(minutes=1))
RESTART = TeploStart(boot_id=BOOT, last_activity=CUT_AT, router_uptime=timedelta(minutes=17))
LATER_RESTART = TeploStart(boot_id=BOOT, last_activity=CUT_AT + timedelta(hours=1), router_uptime=timedelta(hours=1))
EARLIER = TeploStart(boot_id=EARLIER_BOOT, last_activity=CUT_AT - timedelta(days=3), router_uptime=timedelta(days=2))
BEFORE_BOOT_IDS = TeploStart(boot_id=None, last_activity=CUT_AT - timedelta(days=3), router_uptime=None)
UNREAD = TeploStart(boot_id=None, last_activity=CUT_AT, router_uptime=timedelta(minutes=17))


@pytest.mark.parametrize(
    ('current', 'earlier', 'opening'),
    [
        (OPENING, [EARLIER], BootOpening(start=OPENING, previous_boot_id=EARLIER_BOOT)),
        (RESTART, [OPENING, EARLIER], BootOpening(start=OPENING, previous_boot_id=EARLIER_BOOT)),
        (LATER_RESTART, [RESTART, OPENING, EARLIER], BootOpening(start=OPENING, previous_boot_id=EARLIER_BOOT)),
        (RESTART, [OPENING], BootOpening(start=OPENING, previous_boot_id=None)),
        (OPENING, [BEFORE_BOOT_IDS], BootOpening(start=OPENING, previous_boot_id=None)),
        (OPENING, [], BootOpening(start=OPENING, previous_boot_id=None)),
    ],
    ids=[
        'start_after_a_reboot',
        'restart_within_the_boot',
        'second_restart_within_the_boot',
        'boot_of_the_oldest_start',
        'first_start_with_a_boot_id',
        'first_start',
    ],
)
def test_boot_opens_with_the_earliest_start_journaled_with_its_boot_id(
    current: TeploStart,
    earlier: list[TeploStart],
    opening: BootOpening,
) -> None:
    assert boot_opening(current, earlier) == opening


def test_start_without_a_boot_id_opens_its_boot_itself() -> None:
    assert boot_opening(UNREAD, [OPENING, EARLIER]) == BootOpening(start=UNREAD, previous_boot_id=BOOT)


def test_earlier_starts_are_read_only_until_the_boot_changes() -> None:
    earlier = iter([OPENING, EARLIER, BEFORE_BOOT_IDS])

    boot_opening(RESTART, earlier)

    assert list(earlier) == [BEFORE_BOOT_IDS]
