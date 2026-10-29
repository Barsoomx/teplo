from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import ZoneBoosted, ZoneCommandFailed, ZoneCommandIssued
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.readings import ThermostatMode
from apps.telemetry.event_handlers import (
    journal_device_availability,
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
        ZoneCommandFailed(zone='kitchen', change=ThermostatChange(power=False), error='timeout', occurred_at=NOW),
    )

    assert _entry() == (
        'zone_command_failed',
        'kitchen',
        {'zone': 'kitchen', 'change': {'power': False, 'mode': None, 'setpoint': None}, 'error': 'timeout'},
        NOW,
    )


def test_device_availability_is_journaled() -> None:
    journal_device_availability(
        DeviceAvailabilityChanged(slug='thermostat-kitchen', available=False, reason='timeout', occurred_at=NOW),
    )

    assert _entry() == (
        'device_availability_changed',
        'thermostat-kitchen',
        {'slug': 'thermostat-kitchen', 'available': False, 'reason': 'timeout'},
        NOW,
    )


def test_zone_boost_is_journaled() -> None:
    journal_zone_boost(ZoneBoosted(zone='bathroom-1f', until=NOW + timedelta(hours=2), occurred_at=NOW))

    assert _entry() == ('zone_boosted', 'bathroom-1f', {'zone': 'bathroom-1f', 'until': '2026-10-29T08:00:00Z'}, NOW)
