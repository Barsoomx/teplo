import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError

from apps.climate.services.write_gate import KeyedWriteGate
from apps.core.stubs import MonotonicClockStub
from apps.devices.domain.errors import ClimateUnitNotControllableError, DeviceUnavailableError
from apps.devices.stubs import ControllableClimateUnitProviderStub, ControllableClimateUnitStub
from apps.power.services.source_switch import ClimateUnitSourceSwitch
from apps.power.stubs import SourceWriteStampsStub

NOW = datetime(2026, 11, 4, 18, 40, tzinfo=UTC)
WRITTEN_AT = NOW - timedelta(minutes=10)
INTERVAL = timedelta(minutes=5)
START = 1000.0
LOADS_READ_AT = START - 60


@pytest.fixture
def f_unit(request: pytest.FixtureRequest) -> ControllableClimateUnitStub:
    return ControllableClimateUnitStub(state=None, command_errors=getattr(request, 'param', ()))


@pytest.fixture
def f_clock() -> MonotonicClockStub:
    return MonotonicClockStub(now=START)


@pytest.fixture
def f_writes(f_clock: MonotonicClockStub) -> KeyedWriteGate:
    return KeyedWriteGate(clock=f_clock, key_lock=threading.Lock)


@pytest.fixture
def f_stamps(request: pytest.FixtureRequest) -> SourceWriteStampsStub:
    return SourceWriteStampsStub(error=getattr(request, 'param', None))


@pytest.fixture
def f_switch(
    f_unit: ControllableClimateUnitStub,
    f_stamps: SourceWriteStampsStub,
    f_writes: KeyedWriteGate,
) -> ClimateUnitSourceSwitch:
    return ClimateUnitSourceSwitch(
        stamps=f_stamps,
        units=ControllableClimateUnitProviderStub(units={'heater-office': f_unit}),
        writes=f_writes,
    )


@pytest.fixture
def f_switch_written_a_minute_ago(
    f_switch: ClimateUnitSourceSwitch, f_writes: KeyedWriteGate
) -> ClimateUnitSourceSwitch:
    f_writes.reserve(
        'convector-office',
        NOW - timedelta(minutes=1),
        stamped_at=WRITTEN_AT,
        read_at=START,
        interval=INTERVAL,
    )
    return f_switch


@pytest.fixture
def f_switch_after_a_shed(f_switch: ClimateUnitSourceSwitch, f_clock: MonotonicClockStub) -> ClimateUnitSourceSwitch:
    f_switch.switch_off('convector-office', 'heater-office', NOW)
    f_clock.advance(60)
    return f_switch


def test_shed_switches_the_unit_off_at_once_and_stamps_it(
    f_switch_written_a_minute_ago: ClimateUnitSourceSwitch,
    f_unit: ControllableClimateUnitStub,
    f_stamps: SourceWriteStampsStub,
    f_writes: KeyedWriteGate,
    f_clock: MonotonicClockStub,
) -> None:
    stamped = f_switch_written_a_minute_ago.switch_off('convector-office', 'heater-office', NOW)

    assert (
        stamped,
        f_unit.commands,
        f_stamps.sheds,
        f_writes.written_between('convector-office', START, f_clock()),
    ) == (
        True,
        [('power', False)],
        [('convector-office', NOW)],
        True,
    )


@pytest.mark.parametrize('f_stamps', [OperationalError('database is locked')], indirect=True)
def test_shed_whose_stamp_the_database_refuses_still_switches_the_unit_off_and_says_so(
    f_switch: ClimateUnitSourceSwitch,
    f_unit: ControllableClimateUnitStub,
) -> None:
    stamped = f_switch.switch_off('convector-office', 'heater-office', NOW)

    assert (stamped, f_unit.commands) == (False, [('power', False)])


def test_switch_on_stamps_the_command_before_it_switches_the_unit_on(
    f_switch: ClimateUnitSourceSwitch,
    f_unit: ControllableClimateUnitStub,
    f_stamps: SourceWriteStampsStub,
) -> None:
    switched = f_switch.switch_on(
        'convector-office',
        'heater-office',
        NOW,
        stamped_at=WRITTEN_AT,
        read_at=LOADS_READ_AT,
        interval=INTERVAL,
    )

    assert (switched, f_stamps.stamps, f_unit.commands) == (
        True,
        [('convector-office', NOW, True, None)],
        [('power', True)],
    )


def test_switch_on_waits_for_the_source_s_interval_after_its_shed(
    f_switch_after_a_shed: ClimateUnitSourceSwitch,
    f_unit: ControllableClimateUnitStub,
) -> None:
    switched = f_switch_after_a_shed.switch_on(
        'convector-office',
        'heater-office',
        NOW + timedelta(minutes=1),
        stamped_at=NOW,
        read_at=START + 60,
        interval=INTERVAL,
    )

    assert (switched, f_unit.commands) == (False, [('power', False)])


@pytest.mark.parametrize('f_unit', [(DeviceUnavailableError('timed out'),)], indirect=True)
def test_switch_on_that_fails_keeps_its_stamp(
    f_switch: ClimateUnitSourceSwitch,
    f_stamps: SourceWriteStampsStub,
) -> None:
    with pytest.raises(DeviceUnavailableError):
        f_switch.switch_on(
            'convector-office',
            'heater-office',
            NOW,
            stamped_at=WRITTEN_AT,
            read_at=LOADS_READ_AT,
            interval=INTERVAL,
        )

    assert f_stamps.stamps == [('convector-office', NOW, True, None)]


def test_shed_of_a_unit_teplo_cannot_command_fails_before_taking_the_gate(
    f_switch: ClimateUnitSourceSwitch,
    f_writes: KeyedWriteGate,
    f_clock: MonotonicClockStub,
) -> None:
    with pytest.raises(ClimateUnitNotControllableError):
        f_switch.switch_off('convector-office', 'heater-attic', NOW)

    assert f_writes.written_between('convector-office', START - 300, f_clock()) is False
