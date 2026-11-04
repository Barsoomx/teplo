from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.climate.domain.learning import AirSample, Morning
from apps.climate.domain.memory import ZoneMemory, fenced
from apps.climate.domain.modes import AirState, ControlPath, ScheduleMode
from apps.climate.domain.observation import StopWatch

COMFORT_AT = datetime(2026, 11, 4, 6, 30, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def f_air_memory() -> ZoneMemory:
    return ZoneMemory(path=ControlPath.AIR, air_state=AirState.HEAT, heat_target=Decimal('22.0'))


def test_memory_keeps_air_state_and_heat_target_for_its_path(f_air_memory: ZoneMemory) -> None:
    assert (f_air_memory.air_state_on(ControlPath.AIR), f_air_memory.heat_target_on(ControlPath.AIR)) == (
        AirState.HEAT,
        Decimal('22.0'),
    )


@pytest.mark.parametrize('path', [ControlPath.FLOOR, ControlPath.AIR_DRY_RUN])
def test_zone_entering_another_path_starts_without_air_state(f_air_memory: ZoneMemory, path: ControlPath) -> None:
    assert (f_air_memory.air_state_on(path), f_air_memory.heat_target_on(path)) == (None, None)


@pytest.fixture
def f_watching_memory() -> ZoneMemory:
    return ZoneMemory(
        path=ControlPath.AIR,
        mode=ScheduleMode.PREHEAT,
        morning=Morning(
            comfort_at=COMFORT_AT,
            target=Decimal('22.0'),
            outdoor=Decimal('-3.4'),
            samples=(AirSample(at=COMFORT_AT - timedelta(hours=2), air=Decimal('20.4')),),
        ),
        preheated_for=COMFORT_AT,
        stop=StopWatch(
            stopped_at=COMFORT_AT - timedelta(hours=1),
            target=Decimal('22.0'),
            offset=Decimal('0.10'),
            peak=Decimal('21.9'),
        ),
        resets=1,
    )


def test_fenced_memory_keeps_its_watches_while_the_reset_count_holds(f_watching_memory: ZoneMemory) -> None:
    assert fenced(f_watching_memory, 1) == f_watching_memory


def test_fenced_memory_drops_its_morning_and_stop_watch_after_a_reset(f_watching_memory: ZoneMemory) -> None:
    assert fenced(f_watching_memory, 2) == replace(f_watching_memory, morning=None, stop=None, resets=2)
