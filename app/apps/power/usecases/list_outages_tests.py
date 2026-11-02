from datetime import UTC, datetime, timedelta

import pytest

from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.factories import OutageFactory
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.power.usecases.list_outages import ListOutagesInputDTO, ListOutagesOutputDTO, ListOutagesUseCase

pytestmark = pytest.mark.django_db

EVENING = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
MARKED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def f_outages() -> list[Outage]:
    return [
        OutageFactory.create(
            began_at=EVENING,
            ended_at=EVENING + timedelta(minutes=9),
            cause=OutageCause.GRID,
            cause_marked_at=MARKED_AT,
        ),
        OutageFactory.create(began_at=EVENING + timedelta(hours=3), ended_at=EVENING + timedelta(hours=3, minutes=2)),
    ]


@pytest.fixture
def f_use_case() -> ListOutagesUseCase:
    return ListOutagesUseCase(outages=OutageRepository())


def test_outages_are_listed_newest_first_whatever_their_cause(
    f_use_case: ListOutagesUseCase,
    f_outages: list[Outage],
) -> None:
    output = f_use_case.execute(ListOutagesInputDTO())

    assert output == ListOutagesOutputDTO(
        outages=(
            OutageRecord(
                id=f_outages[1].pk,
                began_at=EVENING + timedelta(hours=3),
                ended_at=EVENING + timedelta(hours=3, minutes=2),
                cause=OutageCause.UNMARKED,
                cause_marked_at=None,
            ),
            OutageRecord(
                id=f_outages[0].pk,
                began_at=EVENING,
                ended_at=EVENING + timedelta(minutes=9),
                cause=OutageCause.GRID,
                cause_marked_at=MARKED_AT,
            ),
        ),
    )
