from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, LoadOverview
from apps.power.domain.load_state import LoadState
from apps.power.domain.loads import AmpsOrigin, LoadKind, LoadPhase, PhaseOrigin

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.loads.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@pytest.fixture
def m_convector_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    convector = LoadOverview(
        slug='convector-office',
        name='Конвектор: кабинет',
        kind=LoadKind.SOURCE,
        zone=None,
        device='heater-office',
        priority=8,
        phase=LoadPhase.L2,
        phase_origin=PhaseOrigin.SET,
        learned_phase=None,
        learned_amps=None,
        learning_samples=0,
        nominal_amps=None,
        amps=Decimal('8.7'),
        amps_origin=AmpsOrigin.RATED,
        state=LoadState.DRAWING,
        live=False,
    )
    with mock.patch('apps.panel.views.api.v1.loads.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(panel_overview, loads=(convector,))
        yield use_case


def test_loads_api_lists_the_loads_in_shedding_order(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/loads/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'boiler-1f',
                'name': 'Бойлер 1 этаж',
                'kind': 'plug',
                'zone': None,
                'device': 'plug-boiler-1f',
                'priority': 1,
                'phase': 'l2',
                'phase_origin': 'learned',
                'learned_phase': 'l2',
                'learned_amps': '6.6',
                'learning_samples': 3,
                'nominal_amps': '6.0',
                'amps': '6.0',
                'amps_origin': 'nominal',
                'state': 'shed_dry_run',
                'live': False,
            },
            {
                'slug': 'kitchen',
                'name': 'Кухня',
                'kind': 'zone',
                'zone': 'kitchen',
                'device': 'thermostat-kitchen',
                'priority': 5,
                'phase': 'unknown',
                'phase_origin': 'unknown',
                'learned_phase': None,
                'learned_amps': None,
                'learning_samples': 0,
                'nominal_amps': None,
                'amps': '6.0',
                'amps_origin': 'default',
                'state': 'drawing',
                'live': True,
            },
        ],
        1,
    )


def test_loads_api_gives_a_heat_source_its_rated_current(
    f_client: Client,
    m_convector_overview_use_case: MagicMock,
) -> None:
    response = f_client.get('/api/v1/loads/')

    assert (response.json(), m_convector_overview_use_case.init.call_count) == (
        [
            {
                'slug': 'convector-office',
                'name': 'Конвектор: кабинет',
                'kind': 'source',
                'zone': None,
                'device': 'heater-office',
                'priority': 8,
                'phase': 'l2',
                'phase_origin': 'set',
                'learned_phase': None,
                'learned_amps': None,
                'learning_samples': 0,
                'nominal_amps': None,
                'amps': '8.7',
                'amps_origin': 'rated',
                'state': 'drawing',
                'live': False,
            },
        ],
        1,
    )


def test_loads_api_is_read_only(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.post('/api/v1/loads/', {'live': True})

    assert (response.status_code, m_overview_use_case.init.call_count) == (405, 0)


def test_loads_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/loads/').status_code == 403
