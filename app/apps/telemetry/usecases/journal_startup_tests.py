from datetime import UTC, datetime, timedelta

import pytest
from django.core.serializers.json import DjangoJSONEncoder

from apps.telemetry.factories import JournalEntryFactory
from apps.telemetry.models import JournalEntry
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.usecases.journal_startup import (
    JournalStartupInputDTO,
    JournalStartupOutputDTO,
    JournalStartupUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_previous_entry() -> JournalEntry:
    return JournalEntryFactory.create(kind='zone_boosted', created_at=NOW - timedelta(hours=2))


@pytest.fixture
def f_use_case() -> JournalStartupUseCase:
    return JournalStartupUseCase(journal=JournalRepository(), samples=SampleRepository())


def test_startup_journals_last_activity(f_use_case: JournalStartupUseCase, f_previous_entry: JournalEntry) -> None:
    output = f_use_case.execute(JournalStartupInputDTO(now=NOW))

    started = JournalEntry.objects.get(kind='teplo_started')
    assert (output, started.payload, started.created_at) == (
        JournalStartupOutputDTO(last_activity=f_previous_entry.created_at),
        {'last_activity': DjangoJSONEncoder().default(f_previous_entry.created_at)},
        NOW,
    )
