from datetime import UTC, date, datetime, timedelta

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
from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.repositories.journal import JournalRepository

pytestmark = pytest.mark.django_db

MORNING = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


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
    return GetJournalTimelineUseCase(journal=JournalRepository(), zones=ZoneRepository(), devices=DeviceRepository())


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
    return GetJournalTimelineUseCase(journal=JournalRepository(), zones=ZoneRepository(), devices=DeviceRepository())


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
