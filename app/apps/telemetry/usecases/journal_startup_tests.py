from datetime import UTC, datetime, timedelta

import pytest
from django.core.serializers.json import DjangoJSONEncoder

from apps.telemetry.factories import JournalEntryFactory, SampleFactory
from apps.telemetry.models import JournalEntry, Sample
from apps.telemetry.repositories.journal import JournalRepository
from apps.telemetry.repositories.sample import SampleRepository
from apps.telemetry.usecases.journal_startup import (
    JournalStartupInputDTO,
    JournalStartupOutputDTO,
    JournalStartupUseCase,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
LAGGING_BOOT = NOW - timedelta(hours=5)
CARRIED_ACTIVITY = '2026-10-29T05:29:59.982Z'
BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
ROUTER_UPTIME = timedelta(minutes=2, seconds=31.5)
STARTED = JournalStartupInputDTO(now=NOW, boot_id=BOOT_ID, router_uptime=ROUTER_UPTIME)


@pytest.fixture
def f_previous_entry() -> JournalEntry:
    return JournalEntryFactory.create(kind='zone_boosted', created_at=NOW - timedelta(hours=2))


@pytest.fixture
def f_later_sample(f_previous_entry: JournalEntry) -> Sample:
    return SampleFactory.create(recorded_at=f_previous_entry.created_at + timedelta(hours=1))


@pytest.fixture
def start_on_a_lagging_clock() -> None:
    JournalEntryFactory.create(
        kind='teplo_started',
        subject='',
        payload={'last_activity': CARRIED_ACTIVITY},
        created_at=LAGGING_BOOT,
    )
    SampleFactory.create(recorded_at=LAGGING_BOOT + timedelta(hours=1))


@pytest.fixture
def f_use_case() -> JournalStartupUseCase:
    return JournalStartupUseCase(journal=JournalRepository(), samples=SampleRepository())


def test_startup_journals_last_activity_boot_id_and_router_uptime(
    f_use_case: JournalStartupUseCase,
    f_previous_entry: JournalEntry,
) -> None:
    output = f_use_case.execute(STARTED)

    started = JournalEntry.objects.get(kind='teplo_started')
    assert (output, started.payload, started.created_at) == (
        JournalStartupOutputDTO(last_activity=f_previous_entry.created_at),
        {
            'last_activity': DjangoJSONEncoder().default(f_previous_entry.created_at),
            'boot_id': BOOT_ID,
            'router_uptime': 151.5,
        },
        NOW,
    )


def test_startup_without_a_boot_id_or_uptime_journals_them_as_null(f_use_case: JournalStartupUseCase) -> None:
    f_use_case.execute(JournalStartupInputDTO(now=NOW, boot_id=None, router_uptime=None))

    assert JournalEntry.objects.get(kind='teplo_started').payload == {
        'last_activity': None,
        'boot_id': None,
        'router_uptime': None,
    }


def test_startup_takes_later_sample_as_last_activity(
    f_use_case: JournalStartupUseCase,
    f_later_sample: Sample,
) -> None:
    output = f_use_case.execute(STARTED)

    assert output == JournalStartupOutputDTO(last_activity=f_later_sample.recorded_at)


@pytest.mark.usefixtures('f_previous_entry', 'start_on_a_lagging_clock')
def test_startup_carries_forward_the_last_activity_a_start_on_a_lagging_clock_recorded(
    f_use_case: JournalStartupUseCase,
) -> None:
    output = f_use_case.execute(STARTED)

    started = JournalEntry.objects.filter(kind='teplo_started').latest('pk')
    assert (output, started.payload) == (
        JournalStartupOutputDTO(last_activity=datetime(2026, 10, 29, 5, 29, 59, 982000, tzinfo=UTC)),
        {'last_activity': CARRIED_ACTIVITY, 'boot_id': BOOT_ID, 'router_uptime': 151.5},
    )
