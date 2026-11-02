from datetime import UTC, datetime, timedelta

import pytest

from apps.notifications.domain.outage import Outage, StartOutages, StartupNote, detect_outages
from apps.telemetry.domain.starts import BootOpening, TeploStart

BOOT = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
EARLIER_BOOT = '0b7d3c1e-9a2f-4e6b-8c5d-3f1a2b4c6d8e'
CUT_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
CAME_UP_AT = CUT_AT + timedelta(minutes=9)
OPENED_AT = CAME_UP_AT + timedelta(minutes=1)
STARTED = 50.0
UP_FOR_DAYS = timedelta(days=3)
NOTHING = StartOutages(power_cut=None, restart=None, held_back=False)


def _opening(
    last_activity: datetime | None,
    router_uptime: timedelta | None,
    *,
    boot_id: str | None = BOOT,
    previous_boot_id: str | None = EARLIER_BOOT,
) -> BootOpening:
    return BootOpening(
        start=TeploStart(boot_id=boot_id, last_activity=last_activity, router_uptime=router_uptime),
        previous_boot_id=previous_boot_id,
    )


def _note(last_activity: datetime | None, router_uptime: timedelta | None, boot: BootOpening) -> StartupNote:
    return StartupNote(last_activity=last_activity, monotonic=STARTED, router_uptime=router_uptime, boot=boot)


def _opening_note(
    last_activity: datetime | None,
    router_uptime: timedelta | None,
    *,
    boot_id: str | None = BOOT,
    previous_boot_id: str | None = EARLIER_BOOT,
) -> StartupNote:
    boot = _opening(last_activity, router_uptime, boot_id=boot_id, previous_boot_id=previous_boot_id)
    return _note(last_activity, router_uptime, boot)


def _detect(note: StartupNote, started_at: datetime, checked_after: timedelta = timedelta(minutes=2)) -> StartOutages:
    return detect_outages(note, started_at + checked_after, note.monotonic + checked_after.total_seconds())


def _reported_power_cut(began_at: datetime, ended_at: datetime) -> StartOutages:
    return StartOutages(
        power_cut=Outage(began_at=began_at, ended_at=ended_at, power_cut=True),
        restart=None,
        held_back=False,
    )


def _reported_restart(began_at: datetime, ended_at: datetime) -> StartOutages:
    return StartOutages(
        power_cut=None,
        restart=Outage(began_at=began_at, ended_at=ended_at, power_cut=False),
        held_back=False,
    )


@pytest.mark.parametrize(
    ('down_for', 'reported'),
    [
        (timedelta(minutes=3), False),
        (timedelta(minutes=3, seconds=1), True),
        (timedelta(minutes=9), True),
    ],
)
def test_restart_is_reported_when_longer_than_three_minutes(down_for: timedelta, reported: bool) -> None:
    note = _opening_note(OPENED_AT - down_for, UP_FOR_DAYS, previous_boot_id=None)

    outages = _detect(note, OPENED_AT)

    assert outages == (_reported_restart(OPENED_AT - down_for, OPENED_AT) if reported else NOTHING)


def test_restart_ends_at_the_start_even_when_the_clock_is_trusted_later() -> None:
    note = _opening_note(OPENED_AT - timedelta(minutes=9), UP_FOR_DAYS, previous_boot_id=None)

    outages = _detect(note, OPENED_AT, checked_after=timedelta(hours=2))

    assert outages == _reported_restart(OPENED_AT - timedelta(minutes=9), OPENED_AT)


def test_first_start_has_no_outage() -> None:
    note = _opening_note(None, timedelta(minutes=1), previous_boot_id=None)

    assert _detect(note, OPENED_AT) == NOTHING


@pytest.mark.parametrize(
    ('last_activity', 'outages'),
    [
        (OPENED_AT + timedelta(seconds=1), StartOutages(power_cut=None, restart=None, held_back=True)),
        (OPENED_AT, NOTHING),
    ],
    ids=['stamped_after_the_start', 'stamped_at_the_start'],
)
def test_restart_is_held_back_while_the_start_precedes_the_last_activity(
    last_activity: datetime,
    outages: StartOutages,
) -> None:
    note = _opening_note(last_activity, UP_FOR_DAYS, previous_boot_id=None)

    assert _detect(note, OPENED_AT) == outages


@pytest.mark.parametrize(
    ('router_uptime', 'last_activity'),
    [
        (timedelta(minutes=1), CUT_AT),
        (timedelta(hours=2), OPENED_AT - timedelta(hours=3)),
    ],
    ids=['started_at_the_boot', 'started_hours_after_the_boot'],
)
def test_start_that_opened_a_new_boot_follows_a_power_cut_whatever_the_uptime(
    router_uptime: timedelta,
    last_activity: datetime,
) -> None:
    note = _opening_note(last_activity, router_uptime)

    assert _detect(note, OPENED_AT) == _reported_power_cut(last_activity, OPENED_AT)


def test_power_cut_is_reported_however_short_the_gap() -> None:
    note = _opening_note(OPENED_AT - timedelta(minutes=2), timedelta(minutes=1))

    assert _detect(note, OPENED_AT) == _reported_power_cut(OPENED_AT - timedelta(minutes=2), OPENED_AT)


def test_restart_before_ntp_reports_the_power_cut_from_the_start_that_opened_the_boot() -> None:
    note = _note(CUT_AT, timedelta(minutes=17), _opening(CUT_AT, timedelta(minutes=1)))

    outages = _detect(note, CAME_UP_AT + timedelta(minutes=17), checked_after=timedelta(hours=1))

    assert outages == _reported_power_cut(CUT_AT, OPENED_AT)


@pytest.mark.parametrize(
    ('down_for', 'restart'),
    [
        (timedelta(seconds=10), None),
        (
            timedelta(minutes=5),
            Outage(
                began_at=OPENED_AT + timedelta(minutes=2),
                ended_at=OPENED_AT + timedelta(minutes=7),
                power_cut=False,
            ),
        ),
    ],
    ids=['short_gap', 'long_gap'],
)
def test_restart_after_activity_in_the_boot_reports_its_own_gap_beside_the_power_cut(
    down_for: timedelta,
    restart: Outage | None,
) -> None:
    last_activity = OPENED_AT + timedelta(minutes=2)
    started_at = last_activity + down_for
    note = _note(last_activity, started_at - CAME_UP_AT, _opening(CUT_AT, timedelta(minutes=1)))

    assert _detect(note, started_at) == StartOutages(
        power_cut=Outage(began_at=CUT_AT, ended_at=OPENED_AT, power_cut=True),
        restart=restart,
        held_back=False,
    )


def test_power_cut_is_held_back_while_the_start_that_opened_the_boot_precedes_its_last_activity() -> None:
    note = _note(
        OPENED_AT + timedelta(minutes=5),
        timedelta(minutes=17),
        _opening(OPENED_AT + timedelta(minutes=5), timedelta(minutes=1)),
    )

    outages = _detect(note, CAME_UP_AT + timedelta(minutes=17))

    assert outages == StartOutages(power_cut=None, restart=None, held_back=True)


@pytest.mark.parametrize(
    ('boot_id', 'previous_boot_id'),
    [(BOOT, None), (None, EARLIER_BOOT)],
    ids=['previous_start_without_a_boot_id', 'boot_id_unreadable'],
)
@pytest.mark.parametrize(
    ('router_uptime', 'power_cut'),
    [
        (timedelta(minutes=9, seconds=59), True),
        (timedelta(minutes=10), False),
        (UP_FOR_DAYS, False),
        (None, False),
    ],
    ids=['router_up_under_ten_minutes', 'router_up_ten_minutes', 'router_up_for_days', 'uptime_unknown'],
)
def test_without_two_boot_ids_a_start_follows_a_power_cut_only_when_the_router_came_up_under_ten_minutes_before(
    boot_id: str | None,
    previous_boot_id: str | None,
    router_uptime: timedelta | None,
    power_cut: bool,
) -> None:
    note = _opening_note(
        OPENED_AT - timedelta(minutes=30),
        router_uptime,
        boot_id=boot_id,
        previous_boot_id=previous_boot_id,
    )

    outages = _detect(note, OPENED_AT)

    assert outages == (
        _reported_power_cut(OPENED_AT - timedelta(minutes=30), OPENED_AT)
        if power_cut
        else _reported_restart(OPENED_AT - timedelta(minutes=30), OPENED_AT)
    )


@pytest.mark.parametrize(
    ('last_activity', 'outages'),
    [
        (
            CAME_UP_AT - timedelta(minutes=13),
            _reported_power_cut(CAME_UP_AT - timedelta(minutes=13), CAME_UP_AT + timedelta(minutes=7)),
        ),
        (
            CAME_UP_AT - timedelta(seconds=1),
            _reported_power_cut(CAME_UP_AT - timedelta(seconds=1), CAME_UP_AT + timedelta(minutes=7)),
        ),
        (CAME_UP_AT, _reported_restart(CAME_UP_AT, CAME_UP_AT + timedelta(minutes=7))),
        (
            CAME_UP_AT + timedelta(minutes=3),
            _reported_restart(CAME_UP_AT + timedelta(minutes=3), CAME_UP_AT + timedelta(minutes=7)),
        ),
        (CAME_UP_AT + timedelta(minutes=6, seconds=30), NOTHING),
    ],
    ids=[
        'last_active_before_the_boot',
        'last_active_just_before_the_boot',
        'last_active_as_the_router_came_up',
        'restart_after_a_long_gap',
        'restart_after_a_short_gap',
    ],
)
def test_without_two_boot_ids_a_start_soon_after_a_boot_follows_a_power_cut_only_after_activity_before_the_boot(
    last_activity: datetime,
    outages: StartOutages,
) -> None:
    note = _opening_note(last_activity, timedelta(minutes=7), previous_boot_id=None)

    assert _detect(note, CAME_UP_AT + timedelta(minutes=7)) == outages


def test_power_cut_cannot_be_dated_without_the_uptime_the_start_that_opened_the_boot_journaled() -> None:
    note = _note(CUT_AT, timedelta(minutes=17), _opening(CUT_AT, None))

    outages = _detect(note, CAME_UP_AT + timedelta(minutes=17))

    assert outages == _reported_restart(CUT_AT, CAME_UP_AT + timedelta(minutes=17))
