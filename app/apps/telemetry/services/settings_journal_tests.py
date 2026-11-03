from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest
from django.db import OperationalError

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.core.domain.settings import SettingChange
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.services.settings_journal import SettingsJournal
from apps.telemetry.stubs import JournalWriterStub

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
LOWERED_COMFORT = SettingChange(
    target='zone',
    subject='kitchen',
    name='Кухня',
    field='comfort_floor_setpoint',
    old='28.0',
    new='27.5',
)
SHORTER_LEAD = SettingChange(
    target='zone',
    subject='kitchen',
    name='Кухня',
    field='preheat_lead_minutes',
    old=180,
    new=150,
)


def _lower_comfort(zone: Zone) -> None:
    Zone.objects.filter(pk=zone.pk).update(comfort_floor_setpoint=Decimal('27.5'), preheat_lead_minutes=150)


@pytest.fixture
def f_zone() -> Zone:
    return ZoneFactory.create(slug='kitchen', name='Кухня')


@pytest.fixture
def f_journal() -> SettingsJournal:
    return SettingsJournal(journal=JournalRepository())


@pytest.fixture
def f_locked_journal() -> SettingsJournal:
    return SettingsJournal(journal=JournalWriterStub(errors=(OperationalError('database is locked'),)))


def test_change_is_written_with_one_journal_entry_per_field(f_journal: SettingsJournal, f_zone: Zone) -> None:
    f_journal.record((LOWERED_COMFORT, SHORTER_LEAD), NOW, write=partial(_lower_comfort, f_zone))

    zone = Zone.objects.get(pk=f_zone.pk)
    entries = [
        (entry.kind, entry.subject, entry.payload, entry.created_at) for entry in JournalEntry.objects.order_by('id')
    ]
    assert ((zone.comfort_floor_setpoint, zone.preheat_lead_minutes), entries) == (
        (Decimal('27.5'), 150),
        [
            (
                'settings_changed',
                'kitchen',
                {'object': 'zone', 'name': 'Кухня', 'field': 'comfort_floor_setpoint', 'old': '28.0', 'new': '27.5'},
                NOW,
            ),
            (
                'settings_changed',
                'kitchen',
                {'object': 'zone', 'name': 'Кухня', 'field': 'preheat_lead_minutes', 'old': 180, 'new': 150},
                NOW,
            ),
        ],
    )


def test_change_whose_journal_entry_is_refused_is_not_kept(f_locked_journal: SettingsJournal, f_zone: Zone) -> None:
    with pytest.raises(OperationalError):
        f_locked_journal.record((LOWERED_COMFORT,), NOW, write=partial(_lower_comfort, f_zone))

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (zone.comfort_floor_setpoint, zone.preheat_lead_minutes, JournalEntry.objects.count()) == (
        Decimal('28.0'),
        180,
        0,
    )
