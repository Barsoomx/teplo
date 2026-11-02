from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.usecases.list_outages import ListOutagesOutputDTO

pytestmark = pytest.mark.django_db

EVENING_CUT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
NIGHT_CUT = datetime(2026, 11, 1, 23, 10, tzinfo=UTC)


@pytest.fixture
def m_list_outages_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.outages.ListOutagesUseCase') as use_case:
        use_case.init.return_value.execute.return_value = ListOutagesOutputDTO(
            outages=(
                OutageRecord(
                    id=8,
                    began_at=NIGHT_CUT,
                    ended_at=NIGHT_CUT + timedelta(minutes=2),
                    cause=OutageCause.UNMARKED,
                    cause_marked_at=None,
                ),
                OutageRecord(
                    id=7,
                    began_at=EVENING_CUT,
                    ended_at=EVENING_CUT + timedelta(minutes=9),
                    cause=OutageCause.GRID,
                    cause_marked_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
                ),
            ),
        )
        yield use_case


def test_outages_api_lists_outages_newest_first(f_client: Client, m_list_outages_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/outages/')

    assert (response.status_code, response.json(), m_list_outages_use_case.init.call_count) == (
        200,
        [
            {
                'id': 8,
                'began_at': '2026-11-02T02:10:00+03:00',
                'ended_at': '2026-11-02T02:12:00+03:00',
                'cause': 'unmarked',
                'cause_marked_at': None,
            },
            {
                'id': 7,
                'began_at': '2026-10-31T20:24:00+03:00',
                'ended_at': '2026-10-31T20:33:00+03:00',
                'cause': 'grid',
                'cause_marked_at': '2026-11-02T12:00:00+03:00',
            },
        ],
        1,
    )


def test_outages_api_is_read_only(f_client: Client, m_list_outages_use_case: MagicMock) -> None:
    response = f_client.post('/api/v1/outages/', {'cause': 'grid'})

    assert (response.status_code, m_list_outages_use_case.init.call_count) == (405, 0)


def test_outages_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/outages/').status_code == 403
