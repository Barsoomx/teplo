import os
import threading
import time as clock
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal

import uvicorn
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from apps.climate.models import Zone
from apps.devices.domain.readings import Phase, PlugState, TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.models import Device
from apps.devices.services.phase_meter_store import phase_meter_store
from apps.devices.services.state_store import plug_state_store, temperature_state_store, thermostat_state_store
from apps.house.models import Floor, Room
from apps.power.domain.ev_detection import EvWatch
from apps.power.domain.loads import LoadPhase
from apps.power.models import Load, LoadSettings
from apps.power.services.guard_book import guard_book
from apps.system.services.jobs import guard_phases
from apps.telemetry.models import JournalEntry

ENGLISH_NAMES = {
    'living-room': 'Living room',
    'kitchen': 'Kitchen',
    'hallway': 'Hallway',
    'bathroom-1f': 'Washroom',
    'bathroom-2f': 'Bathroom',
}
ENGLISH_DEVICE_NAMES = {
    'plug-boiler-1f': 'Water heater 1F',
    'plug-boiler-2f': 'Water heater 2F',
    'plug-oven': 'Oven',
    'plug-dishwasher': 'Dishwasher',
    'plug-washing-machine': 'Washing machine',
    'air-living-room': 'Living room air',
    'air-kitchen': 'Kitchen air',
    'air-hallway': 'Hallway air',
    'air-second-floor': 'Bedroom air',
    'outdoor': 'Outdoor',
    'phase-meter': 'Phase meter',
}
ENGLISH_FLOOR_NAMES = {
    'floor-1': '1st floor',
    'floor-2': '2nd floor',
}
ENGLISH_ROOM_NAMES = {
    'open-space': 'Living room, kitchen and hallway',
    'bathroom-1f': 'Washroom',
    'bedroom': 'Bedroom',
    'office': 'Office',
    'bathroom-2f': 'Bathroom',
}
READINGS = {
    'thermostat-living-room': ('28.0', '27.6'),
    'thermostat-kitchen': ('28.0', '25.9'),
    'thermostat-hallway': ('22.0', '21.4'),
    'thermostat-bathroom-2f': ('30.0', '29.2'),
}
PLUG_READINGS = {
    'plug-boiler-1f': ('1305.4', '6.02', '216.8'),
    'plug-boiler-2f': ('0.0', '0.0', '223.1'),
    'plug-oven': ('0.0', '0.0', '221.4'),
    'plug-dishwasher': ('1874.2', '8.47', '219.6'),
    'plug-washing-machine': ('2.1', '0.02', '222.3'),
}
SENSOR_READINGS = {
    'air-living-room': ('22.4', '41.3'),
    'air-kitchen': ('21.6', '44.8'),
    'air-hallway': ('20.9', '39.5'),
    'air-second-floor': ('23.1', '37.2'),
    'outdoor': ('-4.2', None),
}
ENGLISH_LOAD_NAMES = {
    'boiler-1f': 'Water heater 1F',
    'boiler-2f': 'Water heater 2F',
    **ENGLISH_NAMES,
}
LOAD_PHASES = {
    'boiler-1f': {'phase': LoadPhase.L1},
    'boiler-2f': {'learned_phase': LoadPhase.L2, 'learned_amps': Decimal('6.4'), 'learning_samples': 3, 'live': True},
    'hallway': {'learned_phase': LoadPhase.L3, 'learned_amps': Decimal('8.4'), 'learning_samples': 4},
    'kitchen': {'learned_phase': LoadPhase.L1, 'learned_amps': Decimal('7.8'), 'learning_samples': 3},
    'bathroom-2f': {'phase': LoadPhase.L2},
    'living-room': {'phase': LoadPhase.L3, 'nominal_amps': Decimal('8.0')},
}
PHASE_METER = 'phase-meter'
PHASE_CURRENTS = {Phase.A: '26.41', Phase.B: '6.08', Phase.C: '18.40'}
EV_BEFORE = Decimal('2.37')
EV_STEP = Decimal('16.03')
EV_CHARGING_SECONDS = 1200
OFFLINE_THERMOSTAT = 'thermostat-bathroom-1f'
OFFLINE_DETAIL = 'Network Error: Device Unreachable'
OFFLINE_CODE = 'device_unavailable'
REFRESH_SECONDS = 1


def main() -> None:
    now = timezone.localtime()
    call_command('migrate', verbosity=0)
    User.objects.create_user(username='owner', password='demo')  # noqa: S106
    if os.environ.get('DEMO_LANGUAGE') == 'en':
        for slug, name in ENGLISH_NAMES.items():
            Zone.objects.filter(slug=slug).update(name=name)
        for slug, name in ENGLISH_DEVICE_NAMES.items():
            Device.objects.filter(slug=slug).update(name=name)
        for slug, name in ENGLISH_FLOOR_NAMES.items():
            Floor.objects.filter(slug=slug).update(name=name)
        for slug, name in ENGLISH_ROOM_NAMES.items():
            Room.objects.filter(slug=slug).update(name=name)
        for slug, name in ENGLISH_LOAD_NAMES.items():
            Load.objects.filter(slug=slug).update(name=name)
    _seed_zones(now)
    _seed_loads()
    _seed_journal(now)
    threading.Thread(target=_keep_readings_fresh, daemon=True).start()
    uvicorn.run('settings.asgi:application', host='0.0.0.0', port=8088, log_level='warning')  # noqa: S104


def _seed_zones(now: datetime) -> None:
    Zone.objects.filter(slug='bathroom-2f').update(boost_until=now + timedelta(hours=1, minutes=35))
    Zone.objects.exclude(slug='hallway').update(live_control=True)


def _seed_loads() -> None:
    for slug, fields in LOAD_PHASES.items():
        Load.objects.filter(slug=slug).update(**fields)
    LoadSettings.objects.update(load_management_live=True)


def _seed_journal(now: datetime) -> None:
    def at(hour: int, minute: int, days_ago: int = 0) -> datetime:
        return datetime.combine(now.date() - timedelta(days=days_ago), time(hour, minute), tzinfo=now.tzinfo)

    def setpoint(zone: str, value: str, *, dry_run: bool = False) -> dict:
        return {'zone': zone, 'change': {'power': None, 'mode': None, 'setpoint': value}, 'dry_run': dry_run}

    def air_control(air: str, state: str, floor: str, *, mode: str, lead_minutes: int | None) -> dict:
        return {
            'zone': 'kitchen',
            'air': air,
            'target': '22.0',
            'mode': mode,
            'state': state,
            'previous_state': 'heat' if state == 'idle' else 'idle',
            'power': True,
            'setpoint': floor,
            'start_offset': '0.30',
            'early_stop_offset': '0.10',
            'lead_minutes': lead_minutes,
        }

    def guard(*, active: bool, air: str) -> dict:
        return {'zone': 'bathroom-2f', 'active': active, 'air': air, 'setpoint': '30.0'}

    def load(slug: str, **fields: object) -> dict:
        return {'load': slug, 'name': Load.objects.get(slug=slug).name, **fields}

    entries = (
        (at(21, 15, days_ago=1), 'teplo_started', '', {'last_activity': None}),
        (
            at(21, 16, days_ago=1),
            'device_availability_changed',
            'thermostat-kitchen',
            {'slug': 'thermostat-kitchen', 'available': True, 'reason': 'reading received', 'error_code': None},
        ),
        (
            at(21, 16, days_ago=1),
            'device_availability_changed',
            'plug-boiler-1f',
            {'slug': 'plug-boiler-1f', 'available': True, 'reason': 'reading received', 'error_code': None},
        ),
        (
            at(21, 16, days_ago=1),
            'device_availability_changed',
            'air-kitchen',
            {'slug': 'air-kitchen', 'available': True, 'reason': 'reading received', 'error_code': None},
        ),
        (
            at(21, 16, days_ago=1),
            'device_availability_changed',
            PHASE_METER,
            {'slug': PHASE_METER, 'available': True, 'reason': 'reading received', 'error_code': None},
        ),
        (at(23, 30, days_ago=1), 'zone_command_issued', 'living-room', setpoint('living-room', '22.0')),
        (at(23, 30, days_ago=1), 'zone_command_issued', 'kitchen', setpoint('kitchen', '22.0')),
        (at(23, 30, days_ago=1), 'zone_command_issued', 'hallway', setpoint('hallway', '22.0', dry_run=True)),
        (at(3, 30), 'zone_command_issued', 'living-room', setpoint('living-room', '28.0')),
        (at(4, 20), 'second_floor_guard_dry_run', 'bathroom-2f', guard(active=True, air='18.8')),
        (
            at(5, 12),
            'load_phase_learned',
            'boiler-2f',
            load('boiler-2f', phase='l2', amps='6.4', samples=3),
        ),
        (at(5, 30), 'zone_command_issued', 'kitchen', setpoint('kitchen', '28.0')),
        (
            at(5, 30),
            'optimal_start_dry_run',
            'kitchen',
            {
                'zone': 'kitchen',
                'lead_minutes': 140,
                'start': at(6, 10).isoformat(),
                'fixed_start': at(5, 30).isoformat(),
            },
        ),
        (
            at(6, 10),
            'air_control_dry_run',
            'kitchen',
            air_control('20.9', 'heat', '30.0', mode='preheat', lead_minutes=140),
        ),
        (
            at(6, 41),
            'device_availability_changed',
            OFFLINE_THERMOSTAT,
            {'slug': OFFLINE_THERMOSTAT, 'available': False, 'reason': OFFLINE_DETAIL, 'error_code': OFFLINE_CODE},
        ),
        (
            at(6, 45),
            'zone_command_failed',
            'bathroom-1f',
            {
                'zone': 'bathroom-1f',
                'change': {'power': True, 'mode': 'hold', 'setpoint': '18.0'},
                'error': OFFLINE_DETAIL,
                'error_code': OFFLINE_CODE,
            },
        ),
        (
            at(6, 31),
            'start_would_wait_dry_run',
            'living-room',
            load('living-room', phase='l3', amps='21.6', load_amps='8.0', reason='no_headroom'),
        ),
        (at(6, 52), 'ev_charging_started', 'l3', {'phase': 'l3', 'amps': str(EV_STEP), 'before': str(EV_BEFORE)}),
        (at(7, 40), 'second_floor_guard_dry_run', 'bathroom-2f', guard(active=False, air='19.3')),
        (
            at(8, 55),
            'air_control_dry_run',
            'kitchen',
            air_control('21.8', 'idle', '18.0', mode='comfort', lead_minutes=None),
        ),
        (
            now - timedelta(minutes=25),
            'zone_boosted',
            'bathroom-2f',
            {'zone': 'bathroom-2f', 'until': (now + timedelta(hours=1, minutes=35)).isoformat()},
        ),
        (now - timedelta(minutes=24), 'zone_command_issued', 'bathroom-2f', setpoint('bathroom-2f', '30.0')),
    )
    JournalEntry.objects.bulk_create(
        [
            JournalEntry(kind=kind, subject=subject, payload=payload, created_at=created_at)
            for created_at, kind, subject, payload in entries
            if created_at <= now
        ],
    )


def _keep_readings_fresh() -> None:
    _read_devices()
    guard_phases()
    _charge_ev()
    while True:
        clock.sleep(REFRESH_SECONDS)
        _read_devices()
        guard_phases()


def _charge_ev() -> None:
    state = guard_book.state()
    if state is None:
        return

    since = clock.monotonic() - EV_CHARGING_SECONDS
    charge = EvWatch(phase=LoadPhase.L3, since=since, until=since + 15, before=EV_BEFORE, step=EV_STEP, charging=True)
    guard_book.remember(replace(state, ev=(charge,)))


def _read_devices() -> None:
    read_at = timezone.now()
    for slug, (target, floor) in READINGS.items():
        thermostat_state_store.record_state(
            slug,
            ThermostatState(
                power=True,
                mode=ThermostatMode.HOLD,
                setpoint=Decimal(target),
                floor_temperature=Decimal(floor),
                heating=Decimal(floor) < Decimal(target),
                read_at=read_at,
            ),
        )
    thermostat_state_store.record_failure(OFFLINE_THERMOSTAT, error_code=OFFLINE_CODE, error=OFFLINE_DETAIL)
    for slug, (power, current, voltage) in PLUG_READINGS.items():
        plug_state_store.record_state(
            slug,
            PlugState(
                on=True,
                power_watts=Decimal(power),
                current_amps=Decimal(current),
                voltage_volts=Decimal(voltage),
                read_at=read_at,
            ),
        )
    for slug, (temperature, humidity) in SENSOR_READINGS.items():
        temperature_state_store.record_state(
            slug,
            TemperatureReading(
                temperature=Decimal(temperature),
                humidity=None if humidity is None else Decimal(humidity),
                read_at=read_at,
            ),
        )
    for phase, amps in PHASE_CURRENTS.items():
        phase_meter_store.record_current(PHASE_METER, phase, Decimal(amps), read_at)
