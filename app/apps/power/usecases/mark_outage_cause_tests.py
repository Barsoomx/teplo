from datetime import UTC, datetime, timedelta

import pytest

from apps.power.domain.errors import OutageNotFoundError
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.factories import OutageFactory
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository
from apps.power.usecases.mark_outage_cause import (
    MarkOutageCauseInputDTO,
    MarkOutageCauseOutputDTO,
    MarkOutageCauseUseCase,
)

pytestmark = pytest.mark.django_db

BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ENDED_AT = BEGAN_AT + timedelta(minutes=9)
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def f_outage() -> Outage:
    return OutageFactory.create(began_at=BEGAN_AT, ended_at=ENDED_AT)


@pytest.fixture
def f_use_case() -> MarkOutageCauseUseCase:
    return MarkOutageCauseUseCase(outages=OutageRepository())


def test_owner_marks_the_cause_of_an_outage(f_use_case: MarkOutageCauseUseCase, f_outage: Outage) -> None:
    output = f_use_case.execute(MarkOutageCauseInputDTO(outage_id=f_outage.pk, cause=OutageCause.GRID, now=NOW))

    stored = Outage.objects.get(pk=f_outage.pk)
    assert (output, stored.cause, stored.cause_marked_at) == (
        MarkOutageCauseOutputDTO(
            outage=OutageRecord(
                id=f_outage.pk,
                began_at=BEGAN_AT,
                ended_at=ENDED_AT,
                cause=OutageCause.GRID,
                cause_marked_at=NOW,
            ),
        ),
        'grid',
        NOW,
    )


def test_marking_an_unknown_outage_fails(f_use_case: MarkOutageCauseUseCase) -> None:
    with pytest.raises(OutageNotFoundError) as raised:
        f_use_case.execute(MarkOutageCauseInputDTO(outage_id=9999, cause=OutageCause.OVERLOAD, now=NOW))

    assert (raised.value.error_code, raised.value.detail) == ('outage_not_found', 'outage not found: 9999')
