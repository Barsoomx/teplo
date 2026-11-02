from decimal import Decimal

import pytest

from apps.climate.domain.memory import ZoneMemory
from apps.climate.domain.modes import AirState, ControlPath


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
