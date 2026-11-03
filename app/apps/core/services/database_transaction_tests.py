import pytest
from django.db import OperationalError

from apps.climate.factories import ZoneFactory
from apps.climate.models import Zone
from apps.core.services.database_transaction import DatabaseTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def f_transaction() -> DatabaseTransaction:
    return DatabaseTransaction()


def test_transaction_keeps_what_a_block_wrote(f_transaction: DatabaseTransaction) -> None:
    with f_transaction():
        ZoneFactory.create(slug='kitchen')

    assert list(Zone.objects.values_list('slug', flat=True)) == ['kitchen']


def test_transaction_keeps_nothing_of_a_block_that_raises(f_transaction: DatabaseTransaction) -> None:
    with pytest.raises(OperationalError), f_transaction():
        ZoneFactory.create(slug='kitchen')
        raise OperationalError('database is locked')

    assert list(Zone.objects.values_list('slug', flat=True)) == []
