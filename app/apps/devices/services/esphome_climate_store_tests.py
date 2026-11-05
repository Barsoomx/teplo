from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.readings import ClimateMode, ClimateModeWord, ClimateUnitState
from apps.devices.services.esphome_climate_store import EsphomeClimateSnapshot, EsphomeClimateStore

NOW = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)
HEATING = ClimateUnitState(
    mode=ClimateMode.HEAT,
    target=Decimal('20.5'),
    room_temperature=Decimal('19.5'),
    read_at=NOW,
)


def _reboot(store: EsphomeClimateStore, *, target_after: Decimal | None) -> None:
    store.record_failure('ac-bedroom', error_code='device_unavailable', error='the node went offline')
    store.record_mode('ac-bedroom', ClimateMode.OFF, NOW + timedelta(seconds=30))
    store.record_failure(
        'ac-bedroom',
        error_code='sensor_reading_failed',
        error='target_temperature: the node reported nan',
    )
    if target_after is not None:
        store.record_target('ac-bedroom', target_after, NOW + timedelta(seconds=40))


@pytest.fixture
def clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=1000.0)


@pytest.fixture
def f_store(clock: MonotonicClockStub) -> EsphomeClimateStore:
    return EsphomeClimateStore(clock=clock)


@pytest.fixture
def f_mode_only_store(f_store: EsphomeClimateStore) -> EsphomeClimateStore:
    f_store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW)
    f_store.record_room_temperature('ac-bedroom', Decimal('19.5'))
    return f_store


@pytest.fixture
def f_reported_store(f_store: EsphomeClimateStore, clock: MonotonicClockStub) -> EsphomeClimateStore:
    f_store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW)
    f_store.record_room_temperature('ac-bedroom', Decimal('19.5'))
    clock.advance(20)
    f_store.record_target('ac-bedroom', Decimal('20.5'), NOW + timedelta(seconds=20))
    clock.advance(10)
    return f_store


@pytest.fixture
def f_reports_at_the_command(request: pytest.FixtureRequest, f_reported_store: EsphomeClimateStore) -> int:
    reports = f_reported_store.report_count()
    mode, target = request.param
    if mode is not None:
        f_reported_store.record_mode('ac-bedroom', mode, NOW + timedelta(seconds=30))
    if target is not None:
        f_reported_store.record_target('ac-bedroom', target, NOW + timedelta(seconds=30))
    return reports


@pytest.fixture
def f_failed_store(f_reported_store: EsphomeClimateStore) -> EsphomeClimateStore:
    f_reported_store.record_failure('ac-bedroom', error_code='device_unavailable', error='the node went offline')
    return f_reported_store


@pytest.fixture
def f_store_without_a_target(f_reported_store: EsphomeClimateStore) -> EsphomeClimateStore:
    f_reported_store.record_failure(
        'ac-bedroom',
        error_code='sensor_reading_failed',
        error='target_temperature: the node reported nan',
    )
    return f_reported_store


@pytest.fixture
def f_reports_before_a_reboot(request: pytest.FixtureRequest, f_reported_store: EsphomeClimateStore) -> int:
    reports = f_reported_store.report_count()
    _reboot(f_reported_store, target_after=request.param)
    return reports


@pytest.fixture
def f_rebooted_store(f_reported_store: EsphomeClimateStore) -> EsphomeClimateStore:
    _reboot(f_reported_store, target_after=None)
    return f_reported_store


def test_state_exists_only_once_both_a_mode_and_a_target_arrived(f_mode_only_store: EsphomeClimateStore) -> None:
    assert f_mode_only_store.snapshot('ac-bedroom') == EsphomeClimateSnapshot(
        slug='ac-bedroom',
        state=None,
        age=None,
        error_code=None,
        error=None,
    )


def test_state_is_as_old_as_the_older_of_its_mode_and_target(f_reported_store: EsphomeClimateStore) -> None:
    assert f_reported_store.snapshot('ac-bedroom') == EsphomeClimateSnapshot(
        slug='ac-bedroom',
        state=HEATING,
        age=timedelta(seconds=30),
        error_code=None,
        error=None,
    )


def test_newer_mode_leaves_the_state_as_old_as_its_target(
    f_reported_store: EsphomeClimateStore,
    clock: MonotonicClockStub,
) -> None:
    f_reported_store.record_mode('ac-bedroom', ClimateMode.OFF, NOW + timedelta(seconds=30))
    clock.advance(5)

    snapshot = f_reported_store.snapshot('ac-bedroom')
    assert (snapshot.state, snapshot.age) == (
        ClimateUnitState(
            mode=ClimateMode.OFF,
            target=Decimal('20.5'),
            room_temperature=Decimal('19.5'),
            read_at=NOW + timedelta(seconds=20),
        ),
        timedelta(seconds=15),
    )


def test_state_keeps_the_unit_s_own_word_for_a_mode_teplo_lumps_with_another(
    f_reported_store: EsphomeClimateStore,
) -> None:
    f_reported_store.record_mode('ac-bedroom', ClimateMode.OTHER, NOW + timedelta(seconds=30), word=ClimateModeWord.DRY)

    assert f_reported_store.snapshot('ac-bedroom').state == replace(
        HEATING,
        mode=ClimateMode.OTHER,
        read_at=NOW + timedelta(seconds=20),
        mode_word=ClimateModeWord.DRY,
    )


def test_room_temperature_the_node_could_not_read_is_none(f_reported_store: EsphomeClimateStore) -> None:
    f_reported_store.record_room_temperature('ac-bedroom', None)

    assert f_reported_store.snapshot('ac-bedroom').state == replace(HEATING, room_temperature=None)


def test_failure_drops_the_state_until_both_a_mode_and_a_target_arrive_after_it(
    f_failed_store: EsphomeClimateStore,
) -> None:
    failed = f_failed_store.snapshot('ac-bedroom')
    f_failed_store.record_target('ac-bedroom', Decimal('21.0'), NOW + timedelta(seconds=30))
    target_alone = f_failed_store.snapshot('ac-bedroom')
    f_failed_store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW + timedelta(seconds=30))
    whole = f_failed_store.snapshot('ac-bedroom')

    assert (
        (failed.state, failed.age, failed.error_code, failed.error),
        (target_alone.state, target_alone.error_code),
        (whole.state, whole.error_code, whole.error),
    ) == (
        (None, None, 'device_unavailable', 'the node went offline'),
        (None, 'device_unavailable'),
        (replace(HEATING, target=Decimal('21.0'), read_at=NOW + timedelta(seconds=30)), None, None),
    )


@pytest.mark.parametrize(
    'report',
    [
        lambda store: store.record_room_temperature('ac-bedroom', Decimal('19.0')),
        lambda store: store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW + timedelta(seconds=30)),
    ],
    ids=['room_temperature', 'mode'],
)
def test_report_without_a_target_keeps_the_failure_of_the_target_and_gives_no_state(
    f_store_without_a_target: EsphomeClimateStore,
    report: Callable[[EsphomeClimateStore], None],
) -> None:
    report(f_store_without_a_target)

    assert f_store_without_a_target.snapshot('ac-bedroom') == EsphomeClimateSnapshot(
        slug='ac-bedroom',
        state=None,
        age=None,
        error_code='sensor_reading_failed',
        error='target_temperature: the node reported nan',
    )


@pytest.mark.parametrize(
    'f_reports_before_a_reboot',
    [None, Decimal('20.5')],
    ids=['off_and_nan', 'off_and_nan_then_a_target_alone'],
    indirect=True,
)
def test_node_that_goes_offline_and_boots_gives_no_state_and_no_echo_on_its_off_and_nan(
    f_reported_store: EsphomeClimateStore,
    f_reports_before_a_reboot: int,
) -> None:
    echoed = f_reported_store.wait_for(
        'ac-bedroom',
        lambda state: state.mode is ClimateMode.OFF,
        reported_after=f_reports_before_a_reboot,
        timeout=0,
    )

    snapshot = f_reported_store.snapshot('ac-bedroom')
    assert (echoed, snapshot.state, snapshot.error_code) == (False, None, 'sensor_reading_failed')


def test_node_back_from_a_reboot_gives_its_state_once_it_reports_a_mode_and_a_target(
    f_rebooted_store: EsphomeClimateStore,
) -> None:
    f_rebooted_store.record_mode('ac-bedroom', ClimateMode.HEAT, NOW + timedelta(seconds=40))
    f_rebooted_store.record_target('ac-bedroom', Decimal('20.5'), NOW + timedelta(seconds=40))

    assert f_rebooted_store.snapshot('ac-bedroom') == EsphomeClimateSnapshot(
        slug='ac-bedroom',
        state=replace(HEATING, read_at=NOW + timedelta(seconds=40)),
        age=timedelta(0),
        error_code=None,
        error=None,
    )


def test_cleared_failure_is_only_the_one_of_its_code(f_failed_store: EsphomeClimateStore) -> None:
    f_failed_store.clear_failure('ac-bedroom', 'broker_unavailable')
    kept = f_failed_store.snapshot('ac-bedroom').error_code
    f_failed_store.clear_failure('ac-bedroom', 'device_unavailable')

    assert (kept, f_failed_store.snapshot('ac-bedroom').error_code) == ('device_unavailable', None)


@pytest.mark.parametrize(
    ('f_reports_at_the_command', 'slug', 'accepted'),
    [
        ((ClimateMode.HEAT, Decimal('20.5')), 'ac-bedroom', True),
        ((None, None), 'ac-bedroom', False),
        ((ClimateMode.HEAT, None), 'ac-bedroom', False),
        ((None, Decimal('20.5')), 'ac-bedroom', False),
        ((ClimateMode.OFF, Decimal('20.5')), 'ac-bedroom', False),
        ((ClimateMode.HEAT, Decimal('20.5')), 'ac-office', False),
    ],
    ids=[
        'mode_and_target_after_the_command',
        'state_from_before_the_command',
        'only_the_mode_after_the_command',
        'only_the_target_after_the_command',
        'another_state_after_the_command',
        'never_reported',
    ],
    indirect=['f_reports_at_the_command'],
)
def test_wait_for_takes_only_a_state_whose_mode_and_target_both_arrived_after_the_command(
    f_reported_store: EsphomeClimateStore,
    f_reports_at_the_command: int,
    slug: str,
    accepted: bool,
) -> None:
    echoed = f_reported_store.wait_for(
        slug,
        lambda state: state.mode is ClimateMode.HEAT,
        reported_after=f_reports_at_the_command,
        timeout=0,
    )

    assert echoed is accepted
