from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zones_partial.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


def test_zones_partial_renders_cards(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/zones/')

    content = response.content.decode()
    assert (response.status_code, 'Кухня' in content, '<html' in content, m_overview_use_case.init.call_count) == (
        200,
        True,
        False,
        1,
    )
