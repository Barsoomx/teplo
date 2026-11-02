from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError

from apps.power.domain.errors import OutageNotFoundError
from apps.power.domain.outage import OutageCause, OutageRecord, PowerCut
from apps.power.factories import OutageFactory
from apps.power.models import Outage
from apps.power.repositories.outage import OutageRepository

pytestmark = pytest.mark.django_db

EVENING = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
DAY_BEFORE = EVENING - timedelta(days=1)
NIGHT = EVENING + timedelta(hours=3)
MARKED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
BOOT_ID = '5d1c2a7e-0b4f-4c8a-9e3d-6f2b1a0c9d8e'
EVENING_CUT = PowerCut(
    boot_id=BOOT_ID,
    began_at=EVENING,
    ended_at=EVENING + timedelta(minutes=9),
    noticed_at=EVENING + timedelta(minutes=11),
)


@pytest.fixture
def f_outages() -> list[Outage]:
    return [
        OutageFactory.create(began_at=DAY_BEFORE, ended_at=DAY_BEFORE + timedelta(minutes=9)),
        OutageFactory.create(
            began_at=EVENING,
            ended_at=EVENING + timedelta(minutes=9),
            cause=OutageCause.GRID,
            cause_marked_at=MARKED_AT,
        ),
        OutageFactory.create(began_at=NIGHT, ended_at=NIGHT + timedelta(minutes=2)),
    ]


@pytest.fixture
def f_outage() -> Outage:
    return OutageFactory.create(began_at=EVENING, ended_at=EVENING + timedelta(minutes=9))


@pytest.fixture
def f_outage_of_the_boot() -> Outage:
    return OutageFactory.create(boot_id=BOOT_ID, began_at=EVENING, ended_at=EVENING + timedelta(minutes=9))


def _record(outage: Outage) -> OutageRecord:
    stored = Outage.objects.get(pk=outage.pk)
    return OutageRecord(
        id=stored.pk,
        began_at=stored.began_at,
        ended_at=stored.ended_at,
        cause=OutageCause(stored.cause),
        cause_marked_at=stored.cause_marked_at,
    )


def test_added_outage_starts_unmarked_with_the_boot_of_its_power_cut() -> None:
    record = OutageRepository.add(EVENING_CUT)

    stored = Outage.objects.get()
    assert (record, stored.boot_id) == (
        OutageRecord(
            id=stored.pk,
            began_at=EVENING,
            ended_at=EVENING + timedelta(minutes=9),
            cause=OutageCause.UNMARKED,
            cause_marked_at=None,
        ),
        BOOT_ID,
    )


def test_outage_of_a_boot_on_record_is_not_added_again(f_outage_of_the_boot: Outage) -> None:
    record = OutageRepository.add(EVENING_CUT)

    assert (record, list(Outage.objects.values_list('pk', flat=True))) == (None, [f_outage_of_the_boot.pk])


@pytest.mark.usefixtures('f_outage')
def test_outages_without_a_boot_id_are_added_side_by_side() -> None:
    OutageRepository.add(
        PowerCut(boot_id=None, began_at=NIGHT, ended_at=NIGHT + timedelta(minutes=2), noticed_at=NIGHT),
    )

    assert list(Outage.objects.order_by('began_at').values_list('boot_id', 'began_at')) == [
        (None, EVENING),
        (None, NIGHT),
    ]


@pytest.mark.usefixtures('f_outage_of_the_boot')
def test_database_refuses_a_second_outage_of_one_boot() -> None:
    with pytest.raises(IntegrityError):
        OutageFactory.create(boot_id=BOOT_ID, began_at=NIGHT, ended_at=NIGHT + timedelta(minutes=2))


def test_outages_are_listed_newest_first(f_outages: list[Outage]) -> None:
    assert OutageRepository.newest_first() == [_record(f_outages[2]), _record(f_outages[1]), _record(f_outages[0])]


def test_unmarked_outages_leave_out_the_marked_ones(f_outages: list[Outage]) -> None:
    assert OutageRepository.unmarked() == [_record(f_outages[2]), _record(f_outages[0])]


def test_outages_are_found_by_their_ids(f_outages: list[Outage]) -> None:
    found = OutageRepository.by_ids([f_outages[0].pk, f_outages[2].pk, 9999])

    assert found == {f_outages[0].pk: _record(f_outages[0]), f_outages[2].pk: _record(f_outages[2])}


def test_marking_stores_the_cause_and_its_time(f_outage: Outage) -> None:
    record = OutageRepository.mark(f_outage.pk, OutageCause.OVERLOAD, MARKED_AT)

    assert (record, _record(f_outage)) == (
        OutageRecord(
            id=f_outage.pk,
            began_at=EVENING,
            ended_at=EVENING + timedelta(minutes=9),
            cause=OutageCause.OVERLOAD,
            cause_marked_at=MARKED_AT,
        ),
        record,
    )


def test_marking_an_unknown_outage_raises() -> None:
    with pytest.raises(OutageNotFoundError):
        OutageRepository.mark(9999, OutageCause.GRID, MARKED_AT)
