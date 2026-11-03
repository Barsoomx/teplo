import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.core.repositories.write_lock import lock_for_update

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_zones() -> list[Zone]:
    return [ZoneFactory.create(slug='kitchen'), ZoneFactory.create(slug='hallway')]


def test_lock_for_update_writes_the_rows_as_they_are_and_counts_them(f_zones: list[Zone]) -> None:
    with CaptureQueriesContext(connection) as queries:
        locked = lock_for_update(Zone.objects.filter(slug='kitchen'))

    assert (
        locked,
        [query['sql'].split(' ', 1)[0] for query in queries.captured_queries],
        [(zone.slug, zone.updated_at) for zone in Zone.objects.order_by('slug')],
    ) == (1, ['UPDATE'], [(zone.slug, zone.updated_at) for zone in sorted(f_zones, key=lambda zone: zone.slug)])


@pytest.mark.usefixtures('f_zones')
def test_lock_for_update_of_no_row_counts_none() -> None:
    assert lock_for_update(Zone.objects.filter(slug='attic')) == 0
