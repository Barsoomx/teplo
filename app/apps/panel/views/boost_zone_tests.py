from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.domain.errors import ZoneNotFoundError
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_boost_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.boost_zone.BoostZoneUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_missing_zone_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.boost_zone.BoostZoneUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = ZoneNotFoundError('attic')
        yield use_case


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.boost_zone.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


def test_boost_executes_use_case_and_renders_zones(
    f_client: Client,
    m_boost_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post('/zones/kitchen/boost/')

    executed_input = m_boost_use_case.init.return_value.execute.call_args.args[0]
    assert (
        response.status_code,
        executed_input.zone,
        'Кухня' in response.content.decode(),
        m_overview_use_case.init.call_count,
    ) == (200, 'kitchen', True, 1)


def test_boost_unknown_zone_returns_404(
    f_client: Client,
    m_missing_zone_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post('/zones/attic/boost/')

    assert (response.status_code, m_missing_zone_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        404,
        1,
        0,
    )
