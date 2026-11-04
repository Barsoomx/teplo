from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.factories import ZoneFactory
from apps.climate.repositories.zone import ZoneRepository
from apps.devices.domain.kinds import DeviceDriver, DeviceKind
from apps.devices.factories import DeviceFactory
from apps.devices.repositories.device import DeviceRepository
from apps.panel.usecases.get_journal_timeline import (
    GetJournalTimelineInputDTO,
    GetJournalTimelineOutputDTO,
    GetJournalTimelineUseCase,
    TimelineDay,
    TimelineEntry,
)
from apps.power.domain.loads import LoadPhase
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload
from apps.power.factories import OutageFactory, PlugLoadFactory, SourceLoadFactory, ZoneLoadFactory
from apps.power.models import Outage
from apps.power.repositories.load import LoadRepository
from apps.power.repositories.outage import OutageRepository
from apps.power.services.outage_overloads import OutageOverloads
from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

MORNING = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
CUT = MORNING - timedelta(minutes=40)


@pytest.fixture
def f_use_case() -> GetJournalTimelineUseCase:
    ZoneFactory.create(slug='kitchen', name='Кухня', thermostat__slug='thermostat-kitchen')
    JournalEntryFactory.create(kind='teplo_started', subject='', created_at=MORNING - timedelta(hours=10))
    JournalEntryFactory.create(
        kind='device_availability_changed',
        subject='thermostat-kitchen',
        payload={'available': True},
        created_at=MORNING - timedelta(hours=7, minutes=30),
    )
    JournalEntryFactory.create(
        kind='zone_boosted',
        subject='kitchen',
        payload={'until': '2026-10-29T08:00:00Z'},
        created_at=MORNING,
    )
    return GetJournalTimelineUseCase(
        journal=JournalRepository(),
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        loads=LoadRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
    )


@pytest.fixture
def f_plug_use_case() -> GetJournalTimelineUseCase:
    DeviceFactory.create(
        slug='plug-boiler-1f',
        name='Бойлер 1 этаж',
        kind=DeviceKind.PLUG,
        driver=DeviceDriver.TUYA_PLUG,
    )
    JournalEntryFactory.create(
        kind='device_availability_changed',
        subject='plug-boiler-1f',
        payload={'available': False},
        created_at=MORNING,
    )
    return GetJournalTimelineUseCase(
        journal=JournalRepository(),
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        loads=LoadRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
    )


@pytest.fixture
def f_outage() -> Outage:
    return OutageFactory.create(
        began_at=CUT,
        ended_at=CUT + timedelta(minutes=9),
        cause=OutageCause.GRID,
        cause_marked_at=MORNING,
    )


@pytest.fixture
def f_outage_use_case(f_outage: Outage) -> GetJournalTimelineUseCase:
    payload = {'began_at': '2026-10-29T05:20:00Z', 'ended_at': '2026-10-29T05:29:00Z'}
    JournalEntryFactory.create(kind='power_outage', subject='', payload={'outage_id': 9999, **payload}, created_at=CUT)
    JournalEntryFactory.create(
        kind='power_outage',
        subject='',
        payload={'outage_id': f_outage.pk, **payload},
        created_at=MORNING,
    )
    return GetJournalTimelineUseCase(
        journal=JournalRepository(),
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        loads=LoadRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
    )


@pytest.fixture
def f_loads_use_case() -> GetJournalTimelineUseCase:
    PlugLoadFactory.create(slug='boiler-1f', name='Бойлер 1 этаж', device__slug='plug-boiler-1f')
    ZoneLoadFactory.create(slug='kitchen', name='Кухня', zone__slug='kitchen', zone__name='Кухня')
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l2',
        payload={'phase': 'l2', 'amps': '26.4'},
        created_at=MORNING - timedelta(seconds=2),
    )
    JournalEntryFactory.create(
        kind='load_shed_dry_run',
        subject='boiler-1f',
        payload={'load': 'boiler-1f', 'kind': 'plug'},
        created_at=MORNING - timedelta(seconds=1),
    )
    JournalEntryFactory.create(
        kind='load_command_failed',
        subject='kitchen',
        payload={'load': 'kitchen', 'command': 'shed'},
        created_at=MORNING,
    )
    return GetJournalTimelineUseCase(
        journal=JournalRepository(),
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        loads=LoadRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
    )


@pytest.fixture
def f_convector_use_case() -> GetJournalTimelineUseCase:
    SourceLoadFactory.create(
        slug='convector-office',
        name='Конвектор: кабинет',
        heat_source__slug='convector-office',
        heat_source__device__slug='heater-office',
        heat_source__device__name='Конвектор: кабинет',
    )
    JournalEntryFactory.create(
        kind='source_command_issued',
        subject='convector-office',
        payload={'source': 'convector-office', 'setpoint': '22', 'dry_run': True},
        created_at=MORNING - timedelta(seconds=1),
    )
    JournalEntryFactory.create(
        kind='device_availability_changed',
        subject='heater-office',
        payload={'available': False},
        created_at=MORNING,
    )
    return GetJournalTimelineUseCase(
        journal=JournalRepository(),
        zones=ZoneRepository(),
        devices=DeviceRepository(),
        loads=LoadRepository(),
        outages=OutageRepository(),
        overloads=OutageOverloads(journal=JournalRepository()),
    )


@pytest.fixture
def overload_before_the_cut() -> None:
    JournalEntryFactory.create(
        kind='phase_overload',
        subject='l2',
        payload={'phase': 'l2', 'amps': '26.4'},
        created_at=CUT - timedelta(minutes=1),
    )


def test_timeline_names_subjects_and_groups_entries_by_local_day(f_use_case: GetJournalTimelineUseCase) -> None:
    output = f_use_case.execute(GetJournalTimelineInputDTO(limit=10))

    assert output == GetJournalTimelineOutputDTO(
        days=(
            TimelineDay(
                day=date(2026, 10, 29),
                entries=(
                    TimelineEntry(
                        kind='zone_boosted',
                        subject='kitchen',
                        subject_name='Кухня',
                        device_kind=None,
                        payload={'until': '2026-10-29T08:00:00Z'},
                        created_at=MORNING,
                    ),
                    TimelineEntry(
                        kind='device_availability_changed',
                        subject='thermostat-kitchen',
                        subject_name='Кухня',
                        device_kind=DeviceKind.THERMOSTAT,
                        payload={'available': True},
                        created_at=MORNING - timedelta(hours=7, minutes=30),
                    ),
                ),
            ),
            TimelineDay(
                day=date(2026, 10, 28),
                entries=(
                    TimelineEntry(
                        kind='teplo_started',
                        subject='',
                        subject_name='',
                        device_kind=None,
                        payload={},
                        created_at=MORNING - timedelta(hours=10),
                    ),
                ),
            ),
        ),
    )


def test_timeline_stops_at_limit(f_use_case: GetJournalTimelineUseCase) -> None:
    output = f_use_case.execute(GetJournalTimelineInputDTO(limit=1))

    assert [entry.kind for day in output.days for entry in day.entries] == ['zone_boosted']


def test_timeline_names_a_plug_by_its_device(f_plug_use_case: GetJournalTimelineUseCase) -> None:
    output = f_plug_use_case.execute(GetJournalTimelineInputDTO(limit=10))

    assert output.days == (
        TimelineDay(
            day=date(2026, 10, 29),
            entries=(
                TimelineEntry(
                    kind='device_availability_changed',
                    subject='plug-boiler-1f',
                    subject_name='Бойлер 1 этаж',
                    device_kind=DeviceKind.PLUG,
                    payload={'available': False},
                    created_at=MORNING,
                ),
            ),
        ),
    )


def test_timeline_carries_the_current_cause_of_a_journaled_power_outage(
    f_outage_use_case: GetJournalTimelineUseCase,
    f_outage: Outage,
) -> None:
    output = f_outage_use_case.execute(GetJournalTimelineInputDTO(limit=10))

    assert [entry.outage for day in output.days for entry in day.entries] == [
        OutageRecord(
            id=f_outage.pk,
            began_at=CUT,
            ended_at=CUT + timedelta(minutes=9),
            cause=OutageCause.GRID,
            cause_marked_at=MORNING,
        ),
        None,
    ]


@pytest.mark.usefixtures('overload_before_the_cut')
def test_timeline_notes_the_overload_journaled_right_before_a_power_outage(
    f_outage_use_case: GetJournalTimelineUseCase,
) -> None:
    output = f_outage_use_case.execute(GetJournalTimelineInputDTO(limit=10))

    assert [entry.outage.overload for day in output.days for entry in day.entries if entry.outage is not None] == [
        PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('26.4')),
    ]


def test_timeline_names_a_load_and_the_kind_of_device_behind_it(f_loads_use_case: GetJournalTimelineUseCase) -> None:
    output = f_loads_use_case.execute(GetJournalTimelineInputDTO(limit=10))

    assert [(entry.subject, entry.subject_name, entry.device_kind) for day in output.days for entry in day.entries] == [
        ('kitchen', 'Кухня', DeviceKind.THERMOSTAT),
        ('boiler-1f', 'Бойлер 1 этаж', DeviceKind.PLUG),
        ('l2', 'l2', None),
    ]


def test_timeline_names_a_heat_source_and_its_climate_unit(f_convector_use_case: GetJournalTimelineUseCase) -> None:
    output = f_convector_use_case.execute(GetJournalTimelineInputDTO(limit=10))

    assert [(entry.subject, entry.subject_name, entry.device_kind) for day in output.days for entry in day.entries] == [
        ('heater-office', 'Конвектор: кабинет', DeviceKind.CLIMATE_UNIT),
        ('convector-office', 'Конвектор: кабинет', DeviceKind.CLIMATE_UNIT),
    ]
