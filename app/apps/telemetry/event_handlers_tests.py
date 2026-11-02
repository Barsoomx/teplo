from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import (
    AirControlDryRun,
    OptimalStartDryRun,
    SecondFloorGuardDryRun,
    ZoneBoosted,
    ZoneCommandFailed,
    ZoneCommandIssued,
)
from apps.climate.domain.modes import AirState, ScheduleMode
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import ThermostatMode
from apps.notifications.domain.alerts import AlertKind
from apps.notifications.domain.events import AlertCleared, AlertRaised
from apps.telemetry.event_handlers import (
    journal_air_control_dry_run,
    journal_alert_cleared,
    journal_alert_raised,
    journal_device_availability,
    journal_optimal_start_dry_run,
    journal_second_floor_guard_dry_run,
    journal_zone_boost,
    journal_zone_command,
    journal_zone_command_failure,
)
from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


def _entry() -> tuple[str, str, object, datetime]:
    entry = JournalEntry.objects.get()
    return entry.kind, entry.subject, entry.payload, entry.created_at


def test_zone_command_is_journaled_with_change() -> None:
    journal_zone_command(
        ZoneCommandIssued(
            zone='kitchen',
            change=ThermostatChange(mode=ThermostatMode.HOLD, setpoint=Decimal('28.0')),
            dry_run=True,
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'zone_command_issued',
        'kitchen',
        {'zone': 'kitchen', 'change': {'power': None, 'mode': 'hold', 'setpoint': '28.0'}, 'dry_run': True},
        NOW,
    )


def test_zone_command_failure_is_journaled() -> None:
    journal_zone_command_failure(
        ZoneCommandFailed(
            zone='kitchen',
            change=ThermostatChange(power=False),
            error='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'zone_command_failed',
        'kitchen',
        {
            'zone': 'kitchen',
            'change': {'power': False, 'mode': None, 'setpoint': None},
            'error': 'timeout',
            'error_code': 'device_unavailable',
        },
        NOW,
    )


def test_device_availability_is_journaled() -> None:
    journal_device_availability(
        DeviceAvailabilityChanged(
            slug='thermostat-kitchen',
            available=False,
            reason='timeout',
            error_code='device_unavailable',
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'device_availability_changed',
        'thermostat-kitchen',
        {'slug': 'thermostat-kitchen', 'available': False, 'reason': 'timeout', 'error_code': 'device_unavailable'},
        NOW,
    )


def test_zone_boost_is_journaled() -> None:
    journal_zone_boost(ZoneBoosted(zone='bathroom-1f', until=NOW + timedelta(hours=2), occurred_at=NOW))

    assert _entry() == ('zone_boosted', 'bathroom-1f', {'zone': 'bathroom-1f', 'until': '2026-10-29T08:00:00Z'}, NOW)


def test_air_control_dry_run_is_journaled_with_would_be_state() -> None:
    journal_air_control_dry_run(
        AirControlDryRun(
            zone='kitchen',
            air=Decimal('21.4'),
            target=Decimal('22.0'),
            mode=ScheduleMode.PREHEAT,
            state=AirState.HEAT,
            previous_state=AirState.IDLE,
            power=True,
            setpoint=Decimal('30.0'),
            early_stop_offset=Decimal('0.30'),
            lead_minutes=160,
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'air_control_dry_run',
        'kitchen',
        {
            'zone': 'kitchen',
            'air': '21.4',
            'target': '22.0',
            'mode': 'preheat',
            'state': 'heat',
            'previous_state': 'idle',
            'power': True,
            'setpoint': '30.0',
            'early_stop_offset': '0.30',
            'lead_minutes': 160,
        },
        NOW,
    )


def test_optimal_start_dry_run_is_journaled_with_both_starts() -> None:
    journal_optimal_start_dry_run(
        OptimalStartDryRun(
            zone='kitchen',
            lead_minutes=160,
            start=NOW - timedelta(minutes=10),
            fixed_start=NOW - timedelta(minutes=30),
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'optimal_start_dry_run',
        'kitchen',
        {
            'zone': 'kitchen',
            'lead_minutes': 160,
            'start': '2026-10-29T05:50:00Z',
            'fixed_start': '2026-10-29T05:30:00Z',
        },
        NOW,
    )


def test_second_floor_guard_dry_run_is_journaled() -> None:
    journal_second_floor_guard_dry_run(
        SecondFloorGuardDryRun(
            zone='bathroom-2f',
            active=True,
            air=Decimal('18.9'),
            setpoint=Decimal('30.0'),
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'second_floor_guard_dry_run',
        'bathroom-2f',
        {'zone': 'bathroom-2f', 'active': True, 'air': '18.9', 'setpoint': '30.0'},
        NOW,
    )


def test_raised_alert_is_journaled() -> None:
    journal_alert_raised(
        AlertRaised(
            kind=AlertKind.DEVICE_LOST,
            subject='thermostat-kitchen',
            name='Кухня',
            device_kind=DeviceKind.THERMOSTAT,
            since=NOW - timedelta(minutes=12),
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'alert_raised',
        'thermostat-kitchen',
        {
            'kind': 'device_lost',
            'subject': 'thermostat-kitchen',
            'name': 'Кухня',
            'device_kind': 'thermostat',
            'since': '2026-10-29T05:48:00Z',
        },
        NOW,
    )


def test_cleared_alert_is_journaled() -> None:
    journal_alert_cleared(
        AlertCleared(
            kind=AlertKind.ZONE_OFF,
            subject='kitchen',
            name='Кухня',
            device_kind=DeviceKind.THERMOSTAT,
            since=NOW - timedelta(minutes=40),
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'alert_cleared',
        'kitchen',
        {
            'kind': 'zone_off',
            'subject': 'kitchen',
            'name': 'Кухня',
            'device_kind': 'thermostat',
            'since': '2026-10-29T05:20:00Z',
        },
        NOW,
    )
