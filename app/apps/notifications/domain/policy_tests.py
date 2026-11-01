from datetime import UTC, datetime, timedelta

import pytest

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKey, AlertKind, AlertTransition, MonitoredDevice, ZoneHeating
from apps.notifications.domain.policy import (
    AlertChange,
    AlertEvaluation,
    AlertTrack,
    Condition,
    device_condition,
    evaluate_alerts,
    zone_condition,
)

NOW = datetime(2026, 10, 31, 17, 40, tzinfo=UTC)
LAST_READING = NOW - timedelta(minutes=11)
NOTICED = 1000.0
THERMOSTAT_KEY = AlertKey(kind=AlertKind.DEVICE_LOST, subject='thermostat-kitchen')
ZONE_KEY = AlertKey(kind=AlertKind.ZONE_OFF, subject='kitchen')

Tracks = dict[AlertKey, AlertTrack]


def _evaluate(conditions: list[Condition], tracks: Tracks, after: timedelta) -> AlertEvaluation:
    return evaluate_alerts(conditions, tracks, NOW + after, NOTICED + after.total_seconds())


def _thermostat(*, available: bool, last_seen_at: datetime | None) -> MonitoredDevice:
    return MonitoredDevice(
        slug='thermostat-kitchen',
        name='Кухня',
        kind=DeviceKind.THERMOSTAT,
        available=available,
        last_seen_at=last_seen_at,
    )


@pytest.fixture
def f_lost_thermostat() -> MonitoredDevice:
    return _thermostat(available=False, last_seen_at=LAST_READING)


@pytest.fixture
def f_found_thermostat() -> MonitoredDevice:
    return _thermostat(available=True, last_seen_at=NOW)


@pytest.fixture
def f_silent_thermostat() -> MonitoredDevice:
    return _thermostat(available=False, last_seen_at=None)


@pytest.fixture
def f_cold_zone() -> ZoneHeating:
    return ZoneHeating(slug='kitchen', name='Кухня', should_heat=True, power=False)


@pytest.fixture
def f_unknown_zone() -> ZoneHeating:
    return ZoneHeating(slug='kitchen', name='Кухня', should_heat=True, power=None)


@pytest.fixture
def f_idle_zone() -> ZoneHeating:
    return ZoneHeating(slug='kitchen', name='Кухня', should_heat=False, power=False)


@pytest.fixture
def f_noticed_loss(f_lost_thermostat: MonitoredDevice) -> Tracks:
    return _evaluate([device_condition(f_lost_thermostat)], {}, timedelta(0)).tracks


@pytest.fixture
def f_raised_loss(f_lost_thermostat: MonitoredDevice, f_noticed_loss: Tracks) -> Tracks:
    return _evaluate([device_condition(f_lost_thermostat)], f_noticed_loss, timedelta(minutes=2)).tracks


@pytest.fixture
def f_noticed_silence(f_silent_thermostat: MonitoredDevice) -> Tracks:
    return _evaluate([device_condition(f_silent_thermostat)], {}, timedelta(0)).tracks


@pytest.fixture
def f_noticed_cold_zone(f_cold_zone: ZoneHeating) -> Tracks:
    return _evaluate([zone_condition(f_cold_zone)], {}, timedelta(0)).tracks


@pytest.fixture
def f_held_cold_zone(f_unknown_zone: ZoneHeating, f_noticed_cold_zone: Tracks) -> Tracks:
    return _evaluate([zone_condition(f_unknown_zone)], f_noticed_cold_zone, timedelta(minutes=20)).tracks


@pytest.fixture
def f_raised_cold_zone(f_cold_zone: ZoneHeating, f_noticed_cold_zone: Tracks) -> Tracks:
    return _evaluate([zone_condition(f_cold_zone)], f_noticed_cold_zone, timedelta(minutes=35)).tracks


def test_lost_device_waits_for_confirmation(f_lost_thermostat: MonitoredDevice) -> None:
    evaluation = _evaluate([device_condition(f_lost_thermostat)], {}, timedelta(0))

    assert (evaluation.changes, evaluation.tracks) == (
        (),
        {THERMOSTAT_KEY: AlertTrack(since=LAST_READING, noticed_at=NOTICED, raised=False)},
    )


@pytest.mark.parametrize(
    ('elapsed', 'raised'),
    [
        (timedelta(minutes=1, seconds=59), False),
        (timedelta(minutes=2), True),
    ],
)
def test_lost_device_alerts_once_unavailable_for_two_more_minutes(
    f_lost_thermostat: MonitoredDevice,
    f_noticed_loss: Tracks,
    elapsed: timedelta,
    raised: bool,
) -> None:
    evaluation = _evaluate([device_condition(f_lost_thermostat)], f_noticed_loss, elapsed)

    change = AlertChange(
        condition=device_condition(f_lost_thermostat),
        transition=AlertTransition.RAISED,
        since=LAST_READING,
    )
    assert evaluation.changes == ((change,) if raised else ())


def test_raised_device_loss_is_not_repeated(f_lost_thermostat: MonitoredDevice, f_raised_loss: Tracks) -> None:
    evaluation = _evaluate([device_condition(f_lost_thermostat)], f_raised_loss, timedelta(minutes=30))

    assert (evaluation.changes, evaluation.tracks) == ((), f_raised_loss)


def test_device_reading_again_clears_the_alert_once(
    f_found_thermostat: MonitoredDevice,
    f_raised_loss: Tracks,
) -> None:
    evaluation = _evaluate([device_condition(f_found_thermostat)], f_raised_loss, timedelta(minutes=5))

    change = AlertChange(
        condition=device_condition(f_found_thermostat),
        transition=AlertTransition.CLEARED,
        since=LAST_READING,
    )
    assert (evaluation.changes, evaluation.tracks) == ((change,), {})


def test_device_back_before_confirmation_stays_silent(
    f_found_thermostat: MonitoredDevice,
    f_noticed_loss: Tracks,
) -> None:
    evaluation = _evaluate([device_condition(f_found_thermostat)], f_noticed_loss, timedelta(minutes=1))

    assert (evaluation.changes, evaluation.tracks) == ((), {})


def test_clock_step_does_not_count_toward_a_loss(f_lost_thermostat: MonitoredDevice, f_noticed_loss: Tracks) -> None:
    evaluation = evaluate_alerts(
        [device_condition(f_lost_thermostat)],
        f_noticed_loss,
        NOW + timedelta(hours=3),
        NOTICED + 30,
    )

    assert (evaluation.changes, evaluation.tracks) == ((), f_noticed_loss)


@pytest.mark.parametrize(
    ('elapsed', 'raised'),
    [
        (timedelta(minutes=9, seconds=59), False),
        (timedelta(minutes=10), True),
    ],
)
def test_device_without_reading_alerts_ten_minutes_after_teplo_first_saw_it(
    f_silent_thermostat: MonitoredDevice,
    f_noticed_silence: Tracks,
    elapsed: timedelta,
    raised: bool,
) -> None:
    evaluation = _evaluate([device_condition(f_silent_thermostat)], f_noticed_silence, elapsed)

    change = AlertChange(condition=device_condition(f_silent_thermostat), transition=AlertTransition.RAISED, since=NOW)
    assert evaluation.changes == ((change,) if raised else ())


@pytest.mark.parametrize(
    ('should_heat', 'power', 'failing'),
    [
        (True, False, True),
        (True, True, False),
        (True, None, None),
        (False, False, False),
        (False, None, False),
    ],
)
def test_zone_is_off_only_when_it_should_heat_and_reports_power_off(
    should_heat: bool,
    power: bool | None,
    failing: bool | None,
) -> None:
    zone = ZoneHeating(slug='kitchen', name='Кухня', should_heat=should_heat, power=power)

    assert zone_condition(zone).failing is failing


@pytest.mark.parametrize(
    ('elapsed', 'raised'),
    [
        (timedelta(minutes=34, seconds=59), False),
        (timedelta(minutes=35), True),
    ],
)
def test_zone_off_alerts_after_thirty_five_minutes(
    f_cold_zone: ZoneHeating,
    f_noticed_cold_zone: Tracks,
    elapsed: timedelta,
    raised: bool,
) -> None:
    evaluation = _evaluate([zone_condition(f_cold_zone)], f_noticed_cold_zone, elapsed)

    change = AlertChange(condition=zone_condition(f_cold_zone), transition=AlertTransition.RAISED, since=NOW)
    assert evaluation.changes == ((change,) if raised else ())


def test_unknown_power_keeps_the_zone_off_timer(f_unknown_zone: ZoneHeating, f_noticed_cold_zone: Tracks) -> None:
    evaluation = _evaluate([zone_condition(f_unknown_zone)], f_noticed_cold_zone, timedelta(minutes=20))

    assert (evaluation.changes, evaluation.tracks) == ((), f_noticed_cold_zone)


def test_zone_off_counts_through_unknown_power(f_cold_zone: ZoneHeating, f_held_cold_zone: Tracks) -> None:
    evaluation = _evaluate([zone_condition(f_cold_zone)], f_held_cold_zone, timedelta(minutes=35))

    assert evaluation.changes == (
        AlertChange(condition=zone_condition(f_cold_zone), transition=AlertTransition.RAISED, since=NOW),
    )


def test_zone_that_no_longer_should_heat_clears_its_alert(
    f_idle_zone: ZoneHeating,
    f_raised_cold_zone: Tracks,
) -> None:
    evaluation = _evaluate([zone_condition(f_idle_zone)], f_raised_cold_zone, timedelta(minutes=40))

    change = AlertChange(condition=zone_condition(f_idle_zone), transition=AlertTransition.CLEARED, since=NOW)
    assert (evaluation.changes, evaluation.tracks) == ((change,), {})


def test_subject_no_longer_watched_drops_its_alert_silently(f_raised_loss: Tracks) -> None:
    evaluation = _evaluate([], f_raised_loss, timedelta(minutes=5))

    assert (evaluation.changes, evaluation.tracks) == ((), {})


def test_zone_and_its_thermostat_are_tracked_apart(
    f_lost_thermostat: MonitoredDevice,
    f_cold_zone: ZoneHeating,
    f_raised_loss: Tracks,
) -> None:
    evaluation = _evaluate(
        [device_condition(f_lost_thermostat), zone_condition(f_cold_zone)],
        f_raised_loss,
        timedelta(minutes=3),
    )

    assert (evaluation.changes, set(evaluation.tracks)) == ((), {THERMOSTAT_KEY, ZONE_KEY})
