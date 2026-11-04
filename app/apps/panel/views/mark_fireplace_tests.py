from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client
from freezegun import freeze_time

from apps.climate.domain.errors import FireplaceNotFoundError
from apps.climate.domain.modes import CommandSource
from apps.climate.usecases.mark_fireplace import MarkFireplaceInputDTO
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 16, 40, tzinfo=UTC)
URL = '/rooms/open-space/fireplace/'


@pytest.fixture
def m_mark_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.mark_fireplace.MarkFireplaceUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_refusing_mark_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.mark_fireplace.MarkFireplaceUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = FireplaceNotFoundError('open-space')
        yield use_case


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.mark_fireplace.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@freeze_time(NOW)
@pytest.mark.parametrize(('posted', 'lit'), [('1', True), ('0', False)], ids=['lit', 'out'])
def test_mark_goes_to_the_use_case_from_the_panel_and_the_dashboard_refreshes(
    f_client: Client,
    m_mark_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    posted: str,
    lit: bool,
) -> None:
    response = f_client.post(URL, {'lit': posted})

    content = response.content.decode()
    assert (
        response.status_code,
        m_mark_use_case.init.return_value.execute.call_args.args,
        'Кухня' in content,
        '<html' in content,
        m_overview_use_case.init.call_count,
    ) == (
        200,
        (MarkFireplaceInputDTO(room='open-space', lit=lit, now=NOW, source=CommandSource.PANEL),),
        True,
        False,
        1,
    )


@pytest.mark.parametrize('posted', [{'lit': 'yes'}, {'lit': ''}, {}], ids=['unknown', 'empty', 'missing'])
def test_mark_other_than_lit_or_out_is_refused(
    f_client: Client,
    m_mark_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    posted: dict[str, str],
) -> None:
    response = f_client.post(URL, posted)

    assert (response.status_code, m_mark_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        400,
        0,
        0,
    )


def test_mark_of_a_room_without_a_fireplace_returns_404(
    f_client: Client,
    m_refusing_mark_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'lit': '1'})

    assert (response.status_code, m_refusing_mark_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        404,
        1,
        0,
    )


def test_marking_the_fireplace_requires_login(f_anonymous_client: Client, m_mark_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL, {'lit': '1'})

    assert (response.status_code, response['Location'], m_mark_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )
