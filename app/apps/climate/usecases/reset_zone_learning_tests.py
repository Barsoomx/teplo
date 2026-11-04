from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.events import ZoneLearningReset
from apps.climate.domain.learning import HeatUpModel, ZoneLearningEntry, ZoneLearningRecord
from apps.climate.factories import ZoneLearningFactory
from apps.climate.models import ZoneLearning
from apps.climate.stubs import ZoneLearningResetStoreStub
from apps.climate.usecases.reset_zone_learning import (
    ResetZoneLearningInputDTO,
    ResetZoneLearningOutputDTO,
    ResetZoneLearningUseCase,
)
from apps.core.domain.events import DomainEvent, EventDispatcher
from apps.core.stubs import TransactionStub
from apps.telemetry.models import JournalEntry

NOW = datetime(2026, 11, 4, 15, 10, tzinfo=UTC)
LIVING_ROOM = ZoneLearningEntry(
    zone='living-room',
    name='Гостиная',
    record=ZoneLearningRecord(
        early_stop_offset=Decimal('0.25'),
        heat_up=HeatUpModel(
            dead_time=timedelta(minutes=35),
            rates={-5: Decimal('0.290'), -10: Decimal('0.280')},
            mornings=4,
        ),
        resets=1,
    ),
)
KITCHEN = ZoneLearningEntry(zone='kitchen', name='Кухня', record=ZoneLearningRecord.unlearned())


@pytest.fixture
def f_learning() -> ZoneLearningResetStoreStub:
    return ZoneLearningResetStoreStub(zones={'living-room': LIVING_ROOM, 'kitchen': KITCHEN, 'hallway': None})


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_learning: ZoneLearningResetStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> ResetZoneLearningUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneLearningReset, f_events.append)
    return ResetZoneLearningUseCase(learning=f_learning, transaction=f_transaction, dispatcher=dispatcher)


@pytest.fixture
def f_stored_learning() -> ZoneLearning:
    return ZoneLearningFactory.create(
        zone__slug='living-room',
        zone__name='Гостиная',
        early_stop_offset=Decimal('0.25'),
        dead_time=timedelta(minutes=35),
        heat_up_rates={'-5': '0.290'},
        mornings=4,
    )


def test_reset_clears_what_the_zone_learned_and_journals_the_old_values(
    f_use_case: ResetZoneLearningUseCase,
    f_learning: ZoneLearningResetStoreStub,
    f_transaction: TransactionStub,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(ResetZoneLearningInputDTO(zone='living-room', now=NOW))

    assert (output, f_learning.reset_zones, f_events, f_transaction.outcomes) == (
        ResetZoneLearningOutputDTO(reset=True),
        ['living-room'],
        [
            ZoneLearningReset(
                zone='living-room',
                name='Гостиная',
                early_stop_offset=Decimal('0.25'),
                dead_time_minutes=35,
                rates={'-10': '0.280', '-5': '0.290'},
                mornings=4,
                occurred_at=NOW,
            ),
        ],
        ['committed'],
    )


@pytest.mark.parametrize('zone', ['kitchen', 'hallway'], ids=['learned_nothing', 'never_learned'])
def test_reset_of_a_zone_that_learned_nothing_changes_and_journals_nothing(
    f_use_case: ResetZoneLearningUseCase,
    f_learning: ZoneLearningResetStoreStub,
    f_events: list[DomainEvent],
    zone: str,
) -> None:
    output = f_use_case.execute(ResetZoneLearningInputDTO(zone=zone, now=NOW))

    assert (output, f_learning.reset_zones, f_events) == (ResetZoneLearningOutputDTO(reset=False), [], [])


def test_reset_of_an_unknown_zone_raises(
    f_use_case: ResetZoneLearningUseCase,
    f_transaction: TransactionStub,
) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(ResetZoneLearningInputDTO(zone='attic', now=NOW))

    assert f_transaction.outcomes == ['rolled_back']


@pytest.mark.django_db
def test_stored_learning_is_reset_counted_and_journaled(f_stored_learning: ZoneLearning) -> None:
    ResetZoneLearningUseCase.init().execute(ResetZoneLearningInputDTO(zone='living-room', now=NOW))

    stored = ZoneLearning.objects.get(pk=f_stored_learning.pk)
    assert (
        (stored.early_stop_offset, stored.dead_time, stored.heat_up_rates, stored.mornings, stored.resets),
        list(JournalEntry.objects.values_list('kind', 'subject', 'payload')),
    ) == (
        (None, None, {}, 0, 1),
        [
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
            ),
        ],
    )
