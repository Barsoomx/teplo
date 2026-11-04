from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.change import ThermostatChange
from apps.climate.domain.events import (
    AirControlDryRun,
    BackupHeatDryRun,
    ExternalHeatEnded,
    ExternalHeatStarted,
    FireplaceMarkCleared,
    FireplaceMarked,
    GuestsCleared,
    GuestsSet,
    OptimalStartDryRun,
    RoomOverridden,
    RoomOverrideCleared,
    SourceCommandFailed,
    SourceCommandIssued,
    SourceManualChange,
    ZoneBoosted,
    ZoneCommandFailed,
    ZoneCommandIssued,
    ZoneLearningReset,
    ZoneManualChange,
    ZoneMorningRejected,
)
from apps.climate.domain.external_heat import FireEnd
from apps.climate.domain.modes import AirState, CommandSource, ScheduleMode
from apps.core.domain.events import DomainEvent
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import ThermostatMode
from apps.notifications.domain.alerts import AlertKind
from apps.notifications.domain.events import AlertCleared, AlertRaised
from apps.power.domain.events import (
    EvChargingStarted,
    EvChargingStopped,
    LoadCommand,
    LoadCommandFailed,
    LoadPhaseLearned,
    LoadRestored,
    LoadRestoredDryRun,
    LoadShed,
    LoadShedDryRun,
    LoadShedOverridden,
    PhaseCalmed,
    PhaseOverloaded,
    RestoreCause,
    StartWouldWaitDryRun,
)
from apps.power.domain.loads import LoadKind, LoadPhase, ShedReason
from apps.power.domain.start_gate import StartRefusal
from apps.telemetry.event_handlers import (
    journal_air_control_dry_run,
    journal_alert_cleared,
    journal_alert_raised,
    journal_backup_heat_dry_run,
    journal_device_availability,
    journal_ev_charging_started,
    journal_ev_charging_stopped,
    journal_external_heat_ended,
    journal_external_heat_started,
    journal_fireplace_mark_cleared,
    journal_fireplace_marked,
    journal_guests_cleared,
    journal_guests_set,
    journal_load_command_failure,
    journal_load_phase_learned,
    journal_load_restored,
    journal_load_restored_dry_run,
    journal_load_shed,
    journal_load_shed_dry_run,
    journal_load_shed_overridden,
    journal_optimal_start_dry_run,
    journal_phase_calm,
    journal_phase_overload,
    journal_room_overridden,
    journal_room_override_cleared,
    journal_source_command,
    journal_source_command_failure,
    journal_source_manual_change,
    journal_start_would_wait_dry_run,
    journal_zone_boost,
    journal_zone_command,
    journal_zone_command_failure,
    journal_zone_learning_reset,
    journal_zone_manual_change,
    journal_zone_morning_rejected,
)
from apps.telemetry.models import JournalEntry

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
BOILER_SHED = {
    'load': 'boiler-1f',
    'name': 'Бойлер 1 этаж',
    'kind': 'plug',
    'phase': 'l2',
    'amps': '26.4',
    'load_amps': '6.0',
    'reason': 'overload',
    'estimated': False,
}


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


def test_manual_change_on_a_thermostat_is_journaled_with_its_zone_as_the_subject() -> None:
    journal_zone_manual_change(
        ZoneManualChange(
            zone='kitchen',
            name='Кухня',
            power=(True, False),
            mode=('hold', 'smart'),
            setpoint=(Decimal('28.0'), Decimal('25.0')),
            until=NOW + timedelta(hours=2),
            dry_run=True,
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'zone_manual_change',
        'kitchen',
        {
            'zone': 'kitchen',
            'name': 'Кухня',
            'power': [True, False],
            'mode': ['hold', 'smart'],
            'setpoint': ['28.0', '25.0'],
            'until': '2026-10-29T08:00:00Z',
            'dry_run': True,
        },
        NOW,
    )


@pytest.mark.parametrize(
    ('journal', 'event', 'kind', 'payload'),
    [
        (
            journal_source_command,
            SourceCommandIssued(
                source='convector-office',
                name='Конвектор: кабинет',
                power=True,
                setpoint=Decimal(22),
                dry_run=True,
                occurred_at=NOW,
            ),
            'source_command_issued',
            {
                'source': 'convector-office',
                'name': 'Конвектор: кабинет',
                'power': True,
                'setpoint': '22',
                'dry_run': True,
            },
        ),
        (
            journal_source_command_failure,
            SourceCommandFailed(
                source='convector-office',
                name='Конвектор: кабинет',
                power=None,
                setpoint=Decimal(19),
                error='timeout',
                error_code='device_unavailable',
                occurred_at=NOW,
            ),
            'source_command_failed',
            {
                'source': 'convector-office',
                'name': 'Конвектор: кабинет',
                'power': None,
                'setpoint': '19',
                'error': 'timeout',
                'error_code': 'device_unavailable',
            },
        ),
        (
            journal_source_manual_change,
            SourceManualChange(
                source='convector-office',
                name='Конвектор: кабинет',
                power=None,
                setpoint=(Decimal(22), Decimal(25)),
                until=NOW + timedelta(hours=2),
                dry_run=False,
                occurred_at=NOW,
            ),
            'source_manual_change',
            {
                'source': 'convector-office',
                'name': 'Конвектор: кабинет',
                'power': None,
                'setpoint': ['22', '25'],
                'until': '2026-10-29T08:00:00Z',
                'dry_run': False,
            },
        ),
    ],
    ids=['command', 'failed_command', 'manual_change'],
)
def test_heat_source_entries_are_journaled_with_the_source_as_the_subject(
    journal: Callable[..., None],
    event: DomainEvent,
    kind: str,
    payload: dict[str, object],
) -> None:
    journal(event)

    assert _entry() == (kind, 'convector-office', payload, NOW)


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
            start_offset=Decimal('0.30'),
            early_stop_offset=Decimal('0.10'),
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
            'start_offset': '0.30',
            'early_stop_offset': '0.10',
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


def test_backup_heat_dry_run_is_journaled_with_its_zone_as_the_subject() -> None:
    journal_backup_heat_dry_run(
        BackupHeatDryRun(
            room='bedroom',
            room_name='Спальня',
            zone='bathroom-2f',
            active=True,
            air=Decimal('18.9'),
            setpoint=Decimal('30.0'),
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'backup_heat_dry_run',
        'bathroom-2f',
        {
            'room': 'bedroom',
            'room_name': 'Спальня',
            'zone': 'bathroom-2f',
            'active': True,
            'air': '18.9',
            'setpoint': '30.0',
        },
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
            'amps': None,
        },
        NOW,
    )


def test_phase_overload_alert_is_journaled_with_its_current() -> None:
    journal_alert_raised(
        AlertRaised(
            kind=AlertKind.PHASE_OVERLOAD,
            subject='l2',
            name='L2',
            device_kind=DeviceKind.PHASE_METER,
            since=NOW - timedelta(minutes=1),
            amps=Decimal('31.27'),
            occurred_at=NOW,
        ),
    )

    assert _entry() == (
        'alert_raised',
        'l2',
        {
            'kind': 'phase_overload',
            'subject': 'l2',
            'name': 'L2',
            'device_kind': 'phase_meter',
            'since': '2026-10-29T05:59:00Z',
            'amps': '31.27',
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
            'amps': None,
        },
        NOW,
    )


@pytest.mark.parametrize(
    ('handler', 'event', 'expected'),
    [
        (
            journal_phase_overload,
            PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal('26.4'), occurred_at=NOW),
            ('phase_overload', 'l2', {'phase': 'l2', 'amps': '26.4', 'estimated': False}, NOW),
        ),
        (
            journal_phase_overload,
            PhaseOverloaded(phase=LoadPhase.L2, amps=Decimal('24.6'), estimated=True, occurred_at=NOW),
            ('phase_overload', 'l2', {'phase': 'l2', 'amps': '24.6', 'estimated': True}, NOW),
        ),
        (
            journal_phase_calm,
            PhaseCalmed(phase=LoadPhase.L2, amps=Decimal('17.2'), occurred_at=NOW),
            ('phase_calm', 'l2', {'phase': 'l2', 'amps': '17.2', 'estimated': False}, NOW),
        ),
        (
            journal_load_shed,
            LoadShed(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('26.4'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=NOW,
            ),
            ('load_shed', 'boiler-1f', BOILER_SHED, NOW),
        ),
        (
            journal_load_shed_dry_run,
            LoadShedDryRun(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                phase=LoadPhase.L2,
                amps=Decimal('26.4'),
                load_amps=Decimal('6.0'),
                reason=ShedReason.OVERLOAD,
                occurred_at=NOW,
            ),
            ('load_shed_dry_run', 'boiler-1f', BOILER_SHED, NOW),
        ),
        (
            journal_load_restored,
            LoadRestored(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.L2,
                amps=Decimal('10.0'),
                load_amps=Decimal('8.0'),
                reason=ShedReason.OVERLOAD,
                cause=RestoreCause.CALM,
                switched_on=False,
                occurred_at=NOW,
            ),
            (
                'load_restored',
                'kitchen',
                {
                    'load': 'kitchen',
                    'name': 'Кухня',
                    'kind': 'zone',
                    'phase': 'l2',
                    'amps': '10.0',
                    'load_amps': '8.0',
                    'reason': 'overload',
                    'cause': 'calm',
                    'switched_on': False,
                    'estimated': False,
                },
                NOW,
            ),
        ),
        (
            journal_load_restored_dry_run,
            LoadRestoredDryRun(
                load='kitchen',
                name='Кухня',
                kind=LoadKind.ZONE,
                phase=LoadPhase.UNKNOWN,
                amps=None,
                load_amps=Decimal('8.0'),
                reason=ShedReason.METER_LOST,
                cause=RestoreCause.RELEASED,
                switched_on=False,
                occurred_at=NOW,
            ),
            (
                'load_restored_dry_run',
                'kitchen',
                {
                    'load': 'kitchen',
                    'name': 'Кухня',
                    'kind': 'zone',
                    'phase': 'unknown',
                    'amps': None,
                    'load_amps': '8.0',
                    'reason': 'meter_lost',
                    'cause': 'released',
                    'switched_on': False,
                    'estimated': False,
                },
                NOW,
            ),
        ),
        (
            journal_load_shed_overridden,
            LoadShedOverridden(load='boiler-1f', name='Бойлер 1 этаж', phase=LoadPhase.L2, occurred_at=NOW),
            ('load_shed_overridden', 'boiler-1f', {'load': 'boiler-1f', 'name': 'Бойлер 1 этаж', 'phase': 'l2'}, NOW),
        ),
        (
            journal_load_command_failure,
            LoadCommandFailed(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                command=LoadCommand.SHED,
                error='timed out',
                error_code='device_unavailable',
                occurred_at=NOW,
            ),
            (
                'load_command_failed',
                'boiler-1f',
                {
                    'load': 'boiler-1f',
                    'name': 'Бойлер 1 этаж',
                    'command': 'shed',
                    'error': 'timed out',
                    'error_code': 'device_unavailable',
                },
                NOW,
            ),
        ),
        (
            journal_start_would_wait_dry_run,
            StartWouldWaitDryRun(
                load='kitchen',
                name='Кухня',
                phase=LoadPhase.L2,
                amps=Decimal('21.5'),
                load_amps=Decimal('8.0'),
                reason=StartRefusal.NO_HEADROOM,
                estimated=True,
                occurred_at=NOW,
            ),
            (
                'start_would_wait_dry_run',
                'kitchen',
                {
                    'load': 'kitchen',
                    'name': 'Кухня',
                    'phase': 'l2',
                    'amps': '21.5',
                    'load_amps': '8.0',
                    'reason': 'no_headroom',
                    'estimated': True,
                },
                NOW,
            ),
        ),
        (
            journal_load_phase_learned,
            LoadPhaseLearned(
                load='boiler-1f',
                name='Бойлер 1 этаж',
                phase=LoadPhase.L2,
                amps=Decimal('6.2'),
                samples=3,
                occurred_at=NOW,
            ),
            (
                'load_phase_learned',
                'boiler-1f',
                {'load': 'boiler-1f', 'name': 'Бойлер 1 этаж', 'phase': 'l2', 'amps': '6.2', 'samples': 3},
                NOW,
            ),
        ),
        (
            journal_ev_charging_started,
            EvChargingStarted(phase=LoadPhase.L3, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=NOW),
            ('ev_charging_started', 'l3', {'phase': 'l3', 'amps': '16.0', 'before': '4.0'}, NOW),
        ),
        (
            journal_ev_charging_stopped,
            EvChargingStopped(phase=LoadPhase.L3, amps=Decimal('16.0'), before=Decimal('4.0'), occurred_at=NOW),
            ('ev_charging_stopped', 'l3', {'phase': 'l3', 'amps': '16.0', 'before': '4.0'}, NOW),
        ),
    ],
    ids=[
        'phase_overload',
        'phase_overload_by_the_estimate',
        'phase_calm',
        'load_shed',
        'load_shed_dry_run',
        'load_restored',
        'load_restored_dry_run',
        'load_shed_overridden',
        'load_command_failed',
        'start_would_wait_dry_run',
        'load_phase_learned',
        'ev_charging_started',
        'ev_charging_stopped',
    ],
)
def test_guard_decision_is_journaled_at_once_with_its_raw_values(
    handler: Callable[..., None],
    event: DomainEvent,
    expected: tuple[str, str, object, datetime],
) -> None:
    handler(event)

    assert _entry() == expected


@pytest.mark.parametrize(
    ('handler', 'event', 'expected'),
    [
        (
            journal_guests_set,
            GuestsSet(until=NOW + timedelta(hours=8), nights=1, source=CommandSource.PANEL, occurred_at=NOW),
            ('guests_set', '', {'until': '2026-10-29T14:00:00Z', 'nights': 1, 'source': 'panel'}, NOW),
        ),
        (
            journal_guests_cleared,
            GuestsCleared(source=CommandSource.TELEGRAM, occurred_at=NOW),
            ('guests_cleared', '', {'source': 'telegram'}, NOW),
        ),
        (
            journal_room_overridden,
            RoomOverridden(
                room='open-space',
                name='Гостиная, кухня и прихожая',
                target=Decimal('22.5'),
                until=NOW + timedelta(hours=2),
                source=CommandSource.PANEL,
                occurred_at=NOW,
            ),
            (
                'room_overridden',
                'open-space',
                {
                    'room': 'open-space',
                    'name': 'Гостиная, кухня и прихожая',
                    'target': '22.5',
                    'until': '2026-10-29T08:00:00Z',
                    'source': 'panel',
                },
                NOW,
            ),
        ),
        (
            journal_room_override_cleared,
            RoomOverrideCleared(
                room='open-space',
                name='Гостиная, кухня и прихожая',
                source=CommandSource.PANEL,
                occurred_at=NOW,
            ),
            (
                'room_override_cleared',
                'open-space',
                {'room': 'open-space', 'name': 'Гостиная, кухня и прихожая', 'source': 'panel'},
                NOW,
            ),
        ),
    ],
    ids=['guests_set', 'guests_cleared', 'room_overridden', 'room_override_cleared'],
)
def test_owner_intents_are_journaled_with_their_room_as_the_subject(
    handler: Callable[..., None],
    event: DomainEvent,
    expected: tuple[str, str, object, datetime],
) -> None:
    handler(event)

    assert _entry() == expected


@pytest.mark.parametrize(
    ('handler', 'event', 'expected'),
    [
        (
            journal_fireplace_marked,
            FireplaceMarked(
                fireplace='fireplace',
                name='Камин',
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                until=NOW + timedelta(hours=4),
                source=CommandSource.TELEGRAM,
                occurred_at=NOW,
            ),
            (
                'fireplace_marked',
                'fireplace',
                {
                    'fireplace': 'fireplace',
                    'name': 'Камин',
                    'room': 'open-space',
                    'room_name': 'Гостиная, кухня и прихожая',
                    'until': '2026-10-29T10:00:00Z',
                    'source': 'telegram',
                },
                NOW,
            ),
        ),
        (
            journal_fireplace_mark_cleared,
            FireplaceMarkCleared(
                fireplace='fireplace',
                name='Камин',
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                source=CommandSource.PANEL,
                occurred_at=NOW,
            ),
            (
                'fireplace_mark_cleared',
                'fireplace',
                {
                    'fireplace': 'fireplace',
                    'name': 'Камин',
                    'room': 'open-space',
                    'room_name': 'Гостиная, кухня и прихожая',
                    'source': 'panel',
                },
                NOW,
            ),
        ),
        (
            journal_external_heat_started,
            ExternalHeatStarted(
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                rates={'air-kitchen': Decimal('2.10'), 'air-living-room': Decimal('1.62')},
                live=False,
                occurred_at=NOW,
            ),
            (
                'external_heat_started',
                'open-space',
                {
                    'room': 'open-space',
                    'room_name': 'Гостиная, кухня и прихожая',
                    'rates': {'air-kitchen': '2.10', 'air-living-room': '1.62'},
                    'live': False,
                },
                NOW,
            ),
        ),
        (
            journal_external_heat_ended,
            ExternalHeatEnded(
                room='open-space',
                room_name='Гостиная, кухня и прихожая',
                reason=FireEnd.CALM,
                live=True,
                occurred_at=NOW,
            ),
            (
                'external_heat_ended',
                'open-space',
                {'room': 'open-space', 'room_name': 'Гостиная, кухня и прихожая', 'reason': 'calm', 'live': True},
                NOW,
            ),
        ),
        (
            journal_zone_morning_rejected,
            ZoneMorningRejected(
                zone='living-room',
                name='Гостиная',
                rate=Decimal('3.412'),
                cap=Decimal('1.5'),
                outdoor_bucket=-5,
                dead_time_minutes=12,
                occurred_at=NOW,
            ),
            (
                'zone_morning_rejected',
                'living-room',
                {
                    'zone': 'living-room',
                    'name': 'Гостиная',
                    'rate': '3.412',
                    'cap': '1.5',
                    'outdoor_bucket': -5,
                    'dead_time_minutes': 12,
                },
                NOW,
            ),
        ),
        (
            journal_zone_learning_reset,
            ZoneLearningReset(
                zone='living-room',
                name='Гостиная',
                early_stop_offset=Decimal('0.25'),
                dead_time_minutes=35,
                rates={'-5': '0.290'},
                mornings=4,
                occurred_at=NOW,
            ),
            (
                'zone_learning_reset',
                'living-room',
                {
                    'zone': 'living-room',
                    'name': 'Гостиная',
                    'early_stop_offset': '0.25',
                    'dead_time_minutes': 35,
                    'rates': {'-5': '0.290'},
                    'mornings': 4,
                },
                NOW,
            ),
        ),
    ],
    ids=[
        'fireplace_marked',
        'fireplace_mark_cleared',
        'external_heat_started',
        'external_heat_ended',
        'zone_morning_rejected',
        'zone_learning_reset',
    ],
)
def test_fireplace_and_learning_entries_are_journaled_with_their_subjects(
    handler: Callable[..., None],
    event: DomainEvent,
    expected: tuple[str, str, object, datetime],
) -> None:
    handler(event)

    assert _entry() == expected
