from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.power.domain.errors import OutageNotFoundError
from apps.power.domain.outage import OutageCause, OutageRecord
from apps.power.usecases.mark_outage_cause import MarkOutageCauseOutputDTO

pytestmark = pytest.mark.django_db

BEGAN_AT = datetime(2026, 10, 31, 17, 24, tzinfo=UTC)
ACTION = 'hx-post="/journal/outages/7/cause/"'


@pytest.fixture
def m_mark_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal_outage_cause.MarkOutageCauseUseCase') as use_case:
        use_case.init.return_value.execute.return_value = MarkOutageCauseOutputDTO(
            outage=OutageRecord(
                id=7,
                began_at=BEGAN_AT,
                ended_at=BEGAN_AT + timedelta(minutes=9),
                cause=OutageCause.GRID,
                cause_marked_at=BEGAN_AT + timedelta(days=2),
            ),
        )
        yield use_case


@pytest.fixture
def m_missing_outage_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.journal_outage_cause.MarkOutageCauseUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = OutageNotFoundError(7)
        yield use_case


def test_changed_cause_is_shown_with_the_answers_to_change_it_again(
    f_russian_client: Client,
    m_mark_use_case: MagicMock,
) -> None:
    response = f_russian_client.post('/journal/outages/7/cause/', {'cause': 'grid'})

    executed_input = m_mark_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        (executed_input.outage_id, executed_input.cause),
        'Причина: пропало в посёлке' in content,
        content.count(ACTION),
        'value="grid" hx-post="/journal/outages/7/cause/" aria-pressed="true"' in content,
        content.count('aria-pressed="true"'),
        '<html' in content,
    ) == (200, (7, OutageCause.GRID), True, 3, True, 1, False)


@pytest.mark.parametrize('answer', ['unmarked', 'meteor', ''])
def test_answer_other_than_the_three_causes_is_refused(
    f_client: Client,
    m_mark_use_case: MagicMock,
    answer: str,
) -> None:
    response = f_client.post('/journal/outages/7/cause/', {'cause': answer})

    assert (response.status_code, m_mark_use_case.init.call_count) == (400, 0)


def test_answer_for_an_unknown_outage_returns_404(f_client: Client, m_missing_outage_use_case: MagicMock) -> None:
    response = f_client.post('/journal/outages/7/cause/', {'cause': 'overload'})

    assert (response.status_code, m_missing_outage_use_case.init.call_count) == (404, 1)


def test_answer_requires_login(f_anonymous_client: Client, m_mark_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/journal/outages/7/cause/', {'cause': 'grid'})

    assert (response.status_code, response['Location'], m_mark_use_case.init.call_count) == (
        302,
        '/login/?next=/journal/outages/7/cause/',
        0,
    )
