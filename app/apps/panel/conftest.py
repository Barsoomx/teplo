from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import translation

from apps.climate.domain.modes import ControlPath, ScheduleMode, ZoneKind
from apps.climate.domain.schedule import ScheduleTransition
from apps.devices.domain.kinds import DeviceKind
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewOutputDTO,
    PlugOverview,
    SensorOverview,
    ZoneOverview,
    ZoneRegime,
)
from apps.panel.usecases.get_phase_currents import GetPhaseCurrentsOutputDTO, PhaseCurrent, PhaseMeterOverview
from apps.system.domain.clock import ClockStatus, ClockStatusCode


@pytest.fixture
def panel_overview() -> GetPanelOverviewOutputDTO:
    air_kitchen = SensorOverview(
        slug='air-kitchen',
        name='Воздух: кухня',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=True,
        error_code=None,
        error=None,
        temperature=Decimal('21.3'),
        humidity=Decimal('45.2'),
        age_seconds=12,
    )
    air_second_floor = SensorOverview(
        slug='air-second-floor',
        name='Воздух: 2 этаж',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=False,
        error_code='sensor_reading_failed',
        error='temperature: the node reported nan',
        temperature=Decimal('19.8'),
        humidity=None,
        age_seconds=640,
    )
    outdoor = SensorOverview(
        slug='outdoor',
        name='Улица',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=True,
        error_code=None,
        error=None,
        temperature=Decimal('-3.4'),
        humidity=None,
        age_seconds=20,
    )
    return GetPanelOverviewOutputDTO(
        zones=(
            ZoneOverview(
                slug='kitchen',
                name='Кухня',
                kind=ZoneKind.AIR,
                mode=ScheduleMode.COMFORT,
                available=True,
                error_code=None,
                error=None,
                floor_temperature=Decimal('24.5'),
                power=True,
                setpoint=Decimal('28.0'),
                target_setpoint=Decimal('28.0'),
                boosted=False,
                live_control=False,
                air_control_live=False,
                path=ControlPath.AIR_DRY_RUN,
                air_target=Decimal('22.0'),
                regime=ZoneRegime.COMFORT,
                lead=None,
                boost_until=None,
                next_change=ScheduleTransition(
                    mode=ScheduleMode.NIGHT,
                    at=datetime(2026, 10, 29, 23, 30, tzinfo=ZoneInfo('Europe/Moscow')),
                ),
                air=air_kitchen,
            ),
        ),
        plugs=(
            PlugOverview(
                slug='plug-boiler-1f',
                name='Бойлер 1 этаж',
                available=True,
                error_code=None,
                error=None,
                on=True,
                power_watts=Decimal('1305.4'),
                current_amps=Decimal('6.02'),
                voltage_volts=Decimal('216.8'),
            ),
            PlugOverview(
                slug='plug-boiler-2f',
                name='Бойлер 2 этаж',
                available=False,
                error_code='device_protocol_error',
                error='unexpected plug payload: {}',
                on=None,
                power_watts=None,
                current_amps=None,
                voltage_volts=None,
            ),
        ),
        sensors=(air_second_floor, air_kitchen, outdoor),
        outdoor=outdoor,
        standalone_sensors=(air_second_floor,),
        outages=(),
        clock=ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='ntp synchronized'),
        dry_run=True,
    )


@pytest.fixture
def phase_currents() -> GetPhaseCurrentsOutputDTO:
    return GetPhaseCurrentsOutputDTO(
        meter=PhaseMeterOverview(
            stale=False,
            error_code=None,
            error=None,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l2', amps=Decimal('3.05'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l3', amps=Decimal('27.81'), stale=False, age_seconds=2),
            ),
        ),
        scale_amps=Decimal(25),
        start_ceiling_amps=Decimal(20),
        shed_amps=Decimal(24),
    )


@pytest.fixture
def f_russian_client(f_owner: User) -> Iterator[Client]:
    client = Client(headers={'accept-language': 'ru'})
    client.force_login(f_owner)
    with translation.override('ru'):
        yield client
