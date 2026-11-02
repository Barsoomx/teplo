from datetime import UTC, datetime, timedelta

import pytest

from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.power.domain.events import OutageRecorded
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.power.usecases.record_outage import RecordOutageInputDTO, RecordOutageOutputDTO, RecordOutageUseCase

pytestmark = pytest.mark.django_db

BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ENDED_AT = BEGAN_AT + timedelta(minutes=9)
NOW = ENDED_AT + timedelta(minutes=6)


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_events: list[DomainEvent]) -> RecordOutageUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(OutageRecorded, f_events.append)
    return RecordOutageUseCase(outages=OutageRepository(), dispatcher=dispatcher)


def test_outage_is_recorded_unmarked_and_announced(
    f_use_case: RecordOutageUseCase,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(RecordOutageInputDTO(began_at=BEGAN_AT, ended_at=ENDED_AT, now=NOW))

    outage_id = Outage.objects.get().pk
    assert (output, f_events) == (
        RecordOutageOutputDTO(
            outage=OutageRecord(
                id=outage_id,
                began_at=BEGAN_AT,
                ended_at=ENDED_AT,
                cause=OutageCause.UNMARKED,
                cause_marked_at=None,
            ),
        ),
        [OutageRecorded(outage_id=outage_id, began_at=BEGAN_AT, ended_at=ENDED_AT, occurred_at=NOW)],
    )
