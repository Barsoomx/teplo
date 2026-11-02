from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO
from apps.power.domain.errors import OutageNotFoundError
from apps.power.domain.outage import OutageCause

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_mark_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.mark_outage_cause.MarkOutageCauseUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_missing_outage_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.mark_outage_cause.MarkOutageCauseUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = OutageNotFoundError(7)
        yield use_case


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.mark_outage_cause.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@pytest.mark.parametrize(
    ('answer', 'cause'),
    [('overload', OutageCause.OVERLOAD), ('grid', OutageCause.GRID), ('unknown', OutageCause.UNKNOWN)],
)
def test_answer_marks_the_cause_and_refreshes_the_dashboard(
    f_client: Client,
    m_mark_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    answer: str,
    cause: OutageCause,
) -> None:
    response = f_client.post('/outages/7/cause/', {'cause': answer})

    executed_input = m_mark_use_case.init.return_value.execute.call_args.args[0]
    content = response.content.decode()
    assert (
        response.status_code,
        (executed_input.outage_id, executed_input.cause),
        'Кухня' in content,
        '<html' in content,
        m_overview_use_case.init.call_count,
    ) == (200, (7, cause), True, False, 1)


@pytest.mark.parametrize('answer', ['unmarked', 'meteor', ''])
def test_answer_other_than_the_three_causes_is_refused(
    f_client: Client,
    m_mark_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    answer: str,
) -> None:
    response = f_client.post('/outages/7/cause/', {'cause': answer})

    assert (response.status_code, m_mark_use_case.init.call_count, m_overview_use_case.init.call_count) == (400, 0, 0)


def test_answer_for_an_unknown_outage_returns_404(
    f_client: Client,
    m_missing_outage_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post('/outages/7/cause/', {'cause': 'grid'})

    assert (response.status_code, m_missing_outage_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        404,
        1,
        0,
    )


def test_answer_requires_login(f_anonymous_client: Client, m_mark_use_case: MagicMock) -> None:
    response = f_anonymous_client.post('/outages/7/cause/', {'cause': 'grid'})

    assert (response.status_code, response['Location'], m_mark_use_case.init.call_count) == (
        302,
        '/login/?next=/outages/7/cause/',
        0,
    )
