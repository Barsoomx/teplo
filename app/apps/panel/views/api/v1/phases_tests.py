from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_phase_currents import (
    EstimatePartOverview,
    GetPhaseCurrentsOutputDTO,
    PhaseCurrent,
    PhaseMeterOverview,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def m_phases_use_case(phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.phases.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = phase_currents
        yield use_case


@pytest.fixture
def m_estimating_use_case(phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    estimated = replace(
        phase_currents,
        meter=PhaseMeterOverview(
            stale=False,
            error_code=None,
            error=None,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=1),
                PhaseCurrent(
                    phase='l2',
                    amps=Decimal('8.42'),
                    stale=False,
                    age_seconds=1,
                    estimated=True,
                    estimate_parts=(
                        EstimatePartOverview(name='base', amps=Decimal('2.0')),
                        EstimatePartOverview(name='Бойлер 2 этаж', amps=Decimal('6.42')),
                    ),
                ),
                PhaseCurrent(phase='l3', amps=Decimal('3.00'), stale=False, age_seconds=2),
            ),
        ),
    )
    with mock.patch('apps.panel.views.api.v1.phases.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = estimated
        yield use_case


@pytest.fixture
def m_meterless_use_case(phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.phases.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(phase_currents, meter=None)
        yield use_case


def test_phases_api_returns_the_current_of_every_phase(f_client: Client, m_phases_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/phases/')

    assert (response.status_code, response.json(), m_phases_use_case.init.call_count) == (
        200,
        [
            {'phase': 'l1', 'amps': '12.40', 'stale': False, 'age_seconds': 1, 'ev_amps': None, 'estimated': False},
            {'phase': 'l2', 'amps': '3.05', 'stale': False, 'age_seconds': 1, 'ev_amps': None, 'estimated': False},
            {'phase': 'l3', 'amps': '27.81', 'stale': False, 'age_seconds': 2, 'ev_amps': '16.1', 'estimated': False},
        ],
        1,
    )


def test_phases_api_says_which_phase_is_an_estimate(f_client: Client, m_estimating_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/phases/')

    assert (response.status_code, response.json(), m_estimating_use_case.init.call_count) == (
        200,
        [
            {'phase': 'l1', 'amps': '12.40', 'stale': False, 'age_seconds': 1, 'ev_amps': None, 'estimated': False},
            {'phase': 'l2', 'amps': '8.42', 'stale': False, 'age_seconds': 1, 'ev_amps': None, 'estimated': True},
            {'phase': 'l3', 'amps': '3.00', 'stale': False, 'age_seconds': 2, 'ev_amps': None, 'estimated': False},
        ],
        1,
    )


def test_phases_api_is_empty_without_a_phase_meter(f_client: Client, m_meterless_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/phases/')

    assert (response.status_code, response.json(), m_meterless_use_case.init.call_count) == (200, [], 1)


def test_phases_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/phases/').status_code == 403
