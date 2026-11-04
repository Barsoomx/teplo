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

READING_DETAIL = 'l3_current: the node reported nan'


@pytest.fixture
def stale_phase_currents(phase_currents: GetPhaseCurrentsOutputDTO) -> GetPhaseCurrentsOutputDTO:
    return replace(
        phase_currents,
        meter=PhaseMeterOverview(
            stale=True,
            error_code='sensor_reading_failed',
            error=READING_DETAIL,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l2', amps=Decimal('3.05'), stale=False, age_seconds=1),
                PhaseCurrent(phase='l3', amps=Decimal('27.81'), stale=True, age_seconds=14),
            ),
        ),
    )


@pytest.fixture
def estimated_phase_currents(phase_currents: GetPhaseCurrentsOutputDTO) -> GetPhaseCurrentsOutputDTO:
    return replace(
        phase_currents,
        meter=PhaseMeterOverview(
            stale=False,
            error_code=None,
            error=None,
            phases=(
                PhaseCurrent(phase='l1', amps=Decimal('12.40'), stale=False, age_seconds=1),
                PhaseCurrent(
                    phase='l2',
                    amps=Decimal('14.42'),
                    stale=False,
                    age_seconds=1,
                    estimated=True,
                    estimate_parts=(
                        EstimatePartOverview(name='base', amps=Decimal('2.0')),
                        EstimatePartOverview(name='Бойлер 2 этаж', amps=Decimal('6.42')),
                        EstimatePartOverview(name='Санузел 1 этаж', amps=Decimal('6.0')),
                    ),
                ),
                PhaseCurrent(phase='l3', amps=Decimal('3.00'), stale=False, age_seconds=2),
            ),
        ),
    )


@pytest.fixture
def m_estimated_phases_use_case(estimated_phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.phases_partial.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = estimated_phase_currents
        yield use_case


@pytest.fixture
def m_phases_use_case(phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.phases_partial.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = phase_currents
        yield use_case


@pytest.fixture
def m_stale_phases_use_case(stale_phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.phases_partial.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = stale_phase_currents
        yield use_case


@pytest.fixture
def m_meterless_use_case(phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.phases_partial.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(phase_currents, meter=None)
        yield use_case


def test_phases_partial_renders_a_bar_per_phase_and_polls_every_3_seconds(
    f_client: Client,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_client.get('/phases/')

    content = response.content.decode()
    assert (
        response.status_code,
        [label in content for label in ('L1', 'L2', 'L3')],
        ['12.4 A' in content, '3.1 A' in content, '27.8 A' in content],
        content.count('class="phase-fill"'),
        'hx-trigger="every 3s"' in content,
        'is-offline' in content,
        '<html' in content,
        m_phases_use_case.init.call_count,
    ) == (200, [True, True, True], [True, True, True], 3, True, False, False, 1)


def test_phases_partial_hatches_a_stale_meter_and_says_what_to_check(
    f_russian_client: Client,
    m_stale_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/phases/')

    content = response.content.decode()
    assert (
        'class="phase-meter is-offline"' in content,
        'Нет свежих показаний. Проверьте питание фазометра и Wi-Fi.' in content,
        'Фазометр не смог измерить ток фазы' in content,
        ['12,4 А' in content, '3,1 А' in content, '27,8 А' in content],
        content.count('class="phase-fill"'),
        (content.count(READING_DETAIL), f'title="{READING_DETAIL}"' in content),
        m_stale_phases_use_case.init.call_count,
    ) == (True, True, True, [True, True, False], 2, (1, True), 1)


def test_phases_partial_badges_an_ev_on_its_phase_and_names_the_threshold_marks(
    f_russian_client: Client,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/phases/')

    content = response.content.decode()
    l3 = content[content.index('>L3<') :]
    assert (
        content.count('class="chip phase-ev"'),
        'Электромобиль: 16 А' in l3[: l3.index('</li>')],
        'title="Порог включения">20<' in content,
        'title="Порог отключения">24<' in content,
        m_phases_use_case.init.call_count,
    ) == (1, True, True, True, 1)


def test_phases_partial_draws_the_estimated_phase_dashed_with_its_parts_in_the_title(
    f_russian_client: Client,
    m_estimated_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/phases/')

    content = response.content.decode()
    l2 = content[content.index('<li class="phase is-estimated"') :]
    l2 = l2[: l2.index('</li>')]
    assert (
        content.count('is-estimated'),
        'title="база 2,0 А · Бойлер 2 этаж 6,4 А · Санузел 1 этаж 6,0 А"' in l2,
        '≈ 14,4 А' in l2,
        '<span class="phase-estimate">≈ оценка</span>' in l2,
        'class="phase-fill"' in l2,
        'is-offline' in content,
        m_estimated_phases_use_case.init.call_count,
    ) == (1, True, True, True, True, False, 1)


def test_phases_partial_is_empty_without_a_phase_meter(f_client: Client, m_meterless_use_case: MagicMock) -> None:
    response = f_client.get('/phases/')

    assert (response.status_code, response.content.decode().strip(), m_meterless_use_case.init.call_count) == (
        200,
        '',
        1,
    )


def test_phases_partial_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/phases/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/phases/')
