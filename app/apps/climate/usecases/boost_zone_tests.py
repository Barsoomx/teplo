from datetime import UTC, datetime, timedelta

import pytest

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.events import ZoneBoosted
from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.climate.repositories.zone import ZoneRepository
from apps.climate.usecases.boost_zone import BoostZoneInputDTO, BoostZoneOutputDTO, BoostZoneUseCase
from apps.core.domain.events import DomainEvent, EventDispatcher

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def f_zone() -> Zone:
    return ZoneFactory.create(slug='bathroom-1f', last_command_at=NOW - timedelta(minutes=5))


@pytest.fixture
def f_events() -> list[DomainEvent]:
    return []


@pytest.fixture
def f_use_case(f_events: list[DomainEvent]) -> BoostZoneUseCase:
    dispatcher = EventDispatcher()
    dispatcher.register(ZoneBoosted, f_events.append)
    return BoostZoneUseCase(zones=ZoneRepository(), dispatcher=dispatcher)


def test_boost_sets_two_hours_and_clears_write_budget(
    f_use_case: BoostZoneUseCase,
    f_zone: Zone,
    f_events: list[DomainEvent],
) -> None:
    output = f_use_case.execute(BoostZoneInputDTO(zone=f_zone.slug, now=NOW))

    zone = Zone.objects.get(pk=f_zone.pk)
    assert (output, zone.boost_until, zone.last_command_at, f_events) == (
        BoostZoneOutputDTO(zone='bathroom-1f', until=NOW + timedelta(hours=2)),
        NOW + timedelta(hours=2),
        None,
        [ZoneBoosted(zone='bathroom-1f', until=NOW + timedelta(hours=2), occurred_at=NOW)],
    )


def test_boost_unknown_zone_raises(f_use_case: BoostZoneUseCase, f_events: list[DomainEvent]) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(BoostZoneInputDTO(zone='attic', now=NOW))

    assert f_events == []
