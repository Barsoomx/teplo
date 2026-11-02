import os
import threading
import time as clock
from datetime import datetime, time, timedelta
from decimal import Decimal

import uvicorn
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from apps.climate.models import ComfortPeriod, Zone
from apps.devices.domain.readings import Phase, PlugState, TemperatureReading, ThermostatMode, ThermostatState
from apps.devices.models import Device
from apps.devices.services.phase_meter_store import phase_meter_store
from apps.devices.services.state_store import plug_state_store, temperature_state_store, thermostat_state_store
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
    'air-living-room': 'Living room air',
    'air-kitchen': 'Kitchen air',
    'air-hallway': 'Hallway air',
    'air-second-floor': 'Second floor air',
    'outdoor': 'Outdoor',
    'phase-meter': 'Phase meter',
}
COMFORT_STARTS = {
    'living-room': time(6, 30),
    'kitchen': time(8, 30),
    'hallway': time(13, 0),
}
COMFORT_END = time(23, 30)
READINGS = {
    'thermostat-living-room': ('28.0', '27.6'),
    'thermostat-kitchen': ('28.0', '25.9'),
    'thermostat-hallway': ('22.0', '21.4'),
    'thermostat-bathroom-2f': ('30.0', '29.2'),
}
PLUG_READINGS = {
    'plug-boiler-1f': ('1305.4', '6.02', '216.8'),
    'plug-boiler-2f': ('0.0', '0.0', '223.1'),
}
SENSOR_READINGS = {
    'air-living-room': ('22.4', '41.3'),
    'air-kitchen': ('21.6', '44.8'),
    'air-hallway': ('20.9', '39.5'),
    'air-second-floor': ('23.1', '37.2'),
    'outdoor': ('-4.2', None),
}
PHASE_METER = 'phase-meter'
PHASE_CURRENTS = {Phase.A: '18.42', Phase.B: '6.08', Phase.C: '2.37'}
OFFLINE_THERMOSTAT = 'thermostat-bathroom-1f'
OFFLINE_DETAIL = 'Network Error: Device Unreachable'
OFFLINE_CODE = 'device_unavailable'
REFRESH_SECONDS = 2


def main() -> None:
    now = timezone.localtime()
    call_command('migrate', verbosity=0)
    User.objects.create_user(username='owner', password='demo')  # noqa: S106
    if os.environ.get('DEMO_LANGUAGE') == 'en':
        for slug, name in ENGLISH_NAMES.items():
            Zone.objects.filter(slug=slug).update(name=name)
        for slug, name in ENGLISH_DEVICE_NAMES.items():
            Device.objects.filter(slug=slug).update(name=name)
    _seed_zones(now)
    _seed_journal(now)
    threading.Thread(target=_keep_readings_fresh, daemon=True).start()
    uvicorn.run('settings.asgi:application', host='0.0.0.0', port=8088, log_level='warning')  # noqa: S104


def _seed_zones(now: datetime) -> None:
    for slug, start in COMFORT_STARTS.items():
        zone = Zone.objects.get(slug=slug)
        ComfortPeriod.objects.filter(zone=zone).delete()
        ComfortPeriod.objects.bulk_create(
            [ComfortPeriod(zone=zone, weekday=weekday, start=start, end=COMFORT_END) for weekday in range(7)],
        )
    Zone.objects.filter(slug='bathroom-2f').update(boost_until=now + timedelta(hours=1, minutes=35))
    Zone.objects.exclude(slug='hallway').update(live_control=True)


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
            'power': True,
            'setpoint': floor,
            'early_stop_offset': '0.30',
            'lead_minutes': lead_minutes,
        }

    def guard(*, active: bool, air: str) -> dict:
        return {'zone': 'bathroom-2f', 'active': active, 'air': air, 'setpoint': '30.0'}

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
        (at(5, 30), 'zone_command_issued', 'kitchen', setpoint('kitchen', '28.0')),
        (
            at(6, 10),
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
    while True:
        read_at = timezone.now()
        for slug, (target, floor) in READINGS.items():
            thermostat_state_store.record_state(
                slug,
                ThermostatState(
                    power=True,
                    mode=ThermostatMode.HOLD,
                    setpoint=Decimal(target),
                    floor_temperature=Decimal(floor),
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
        clock.sleep(REFRESH_SECONDS)
