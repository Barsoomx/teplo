from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.services.zone_overview import PanelOverview

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_service(panel_overview: PanelOverview) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.zones_partial.ZoneOverviewService') as service:
        service.init.return_value.overview.return_value = panel_overview
        yield service


def test_zones_partial_renders_cards(f_client: Client, m_overview_service: MagicMock) -> None:
    response = f_client.get('/zones/')

    content = response.content.decode()
    assert (response.status_code, 'Кухня' in content, '<html' in content, m_overview_service.init.call_count) == (
        200,
        True,
        False,
        1,
    )
