from collections.abc import Iterator
from dataclasses import replace
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO
from apps.system.domain.clock import ClockStatus, ClockStatusCode

pytestmark = pytest.mark.django_db

RAW_DETAILS = ('Network Error: Device Unreachable', 'secret missing: thermostat-hallway', 'ndmc failed: timed out')
PLUG_DETAIL = 'unexpected plug payload: {}'


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@pytest.fixture
def failing_overview(panel_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    kitchen = replace(panel_overview.zones[0], available=False, error_code='device_unavailable', error=RAW_DETAILS[0])
    hallway = replace(kitchen, slug='hallway', name='Прихожая', error_code='secret_missing', error=RAW_DETAILS[1])
    return replace(
        panel_overview,
        zones=(kitchen, hallway),
        clock=ClockStatus(trusted=False, code=ClockStatusCode.NTP_STATUS_FAILED, reason=RAW_DETAILS[2]),
    )


@pytest.fixture
def m_failing_overview_use_case(failing_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = failing_overview
        yield use_case


def test_dashboard_lists_zones(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/')

    assert (response.status_code, 'Кухня' in response.content.decode(), m_overview_use_case.init.call_count) == (
        200,
        True,
        1,
    )


def test_dashboard_explains_failures_in_words_and_keeps_raw_details_in_tooltips(
    f_russian_client: Client,
    m_failing_overview_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'В файле секретов нет адреса и ключа этого термостата' in content,
        'Нет связи с термостатом' in content,
        '(не удалось узнать статус NTP у роутера): держу комфорт' in content,
        [(content.count(detail), f'title="{detail}"' in content) for detail in RAW_DETAILS],
        m_failing_overview_use_case.init.call_count,
    ) == (True, False, True, [(1, True)] * len(RAW_DETAILS), 1)


def test_dashboard_shows_loads_and_explains_a_lost_plug(
    f_russian_client: Client,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Нагрузки' in content,
        'Бойлер 1 этаж' in content,
        'Включено' in content,
        '1305 Вт' in content,
        '217 В' in content,
        'Нет связи. Проверьте питание розетки и Wi-Fi.' in content,
        'Розетка прислала неожиданный ответ' in content,
        (content.count(PLUG_DETAIL), f'title="{PLUG_DETAIL}"' in content),
        m_overview_use_case.init.call_count,
    ) == (True, True, True, True, True, True, True, (1, True), 1)


def test_dashboard_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/')
