from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.devices.domain.kinds import DeviceKind
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, SensorOverview
from apps.panel.usecases.get_phase_currents import GetPhaseCurrentsOutputDTO
from apps.system.domain.clock import ClockStatus, ClockStatusCode

pytestmark = pytest.mark.django_db

RAW_DETAILS = ('Network Error: Device Unreachable', 'secret missing: thermostat-hallway', 'ndmc failed: timed out')
PLUG_DETAIL = 'unexpected plug payload: {}'
SENSOR_DETAIL = 'temperature: the node reported nan'
BROKER_DETAIL = 'mqtt connection refused: not authorised'


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@pytest.fixture
def m_phases_use_case(phase_currents: GetPhaseCurrentsOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPhaseCurrentsUseCase') as use_case:
        use_case.init.return_value.execute.return_value = phase_currents
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


@pytest.fixture
def lost_air_overview(panel_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    lost_air = SensorOverview(
        slug='air-kitchen',
        name='Воздух: кухня',
        kind=DeviceKind.TEMPERATURE_SENSOR,
        available=False,
        error_code='broker_unavailable',
        error=BROKER_DETAIL,
        temperature=Decimal('21.3'),
        humidity=Decimal('45.2'),
        age_seconds=640,
    )
    kitchen = replace(panel_overview.zones[0], air=lost_air)
    return replace(panel_overview, zones=(kitchen,), outdoor=None, standalone_sensors=())


@pytest.fixture
def m_lost_air_overview_use_case(lost_air_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = lost_air_overview
        yield use_case


def test_dashboard_lists_zones(
    f_client: Client,
    m_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_client.get('/')

    assert (
        response.status_code,
        'Кухня' in response.content.decode(),
        m_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (200, True, 1, 1)


def test_dashboard_explains_failures_in_words_and_keeps_raw_details_in_tooltips(
    f_russian_client: Client,
    m_failing_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'В файле секретов нет адреса и ключа этого термостата' in content,
        'Нет связи с термостатом' in content,
        '(не удалось узнать статус NTP у роутера): держу комфорт' in content,
        [(content.count(detail), f'title="{detail}"' in content) for detail in RAW_DETAILS],
        m_failing_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, False, True, [(1, True)] * len(RAW_DETAILS), 1, 1)


def test_dashboard_shows_loads_and_explains_a_lost_plug(
    f_russian_client: Client,
    m_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
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
        m_phases_use_case.init.call_count,
    ) == (True, True, True, True, True, True, True, (1, True), 1, 1)


def test_dashboard_shows_the_air_beside_the_floor_and_the_outdoor_temperature(
    f_russian_client: Client,
    m_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'На улице -3,4°' in content,
        '21,3' in content,
        '45%' in content,
        'Воздух: 2 этаж: нет связи. Проверьте питание датчика и Wi-Fi.' in content,
        'Датчик не смог снять показания' in content,
        (content.count(SENSOR_DETAIL), f'title="{SENSOR_DETAIL}"' in content),
        m_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, True, True, True, True, (1, True), 1, 1)


def test_dashboard_hatches_a_lost_air_sensor_and_says_what_to_check(
    f_russian_client: Client,
    m_lost_air_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'class="air is-offline"' in content,
        'Нет связи с датчиком воздуха. Проверьте его питание и Wi-Fi.' in content,
        'teplo не может подключиться к MQTT-брокеру' in content,
        '21,3' in content,
        'На улице' in content,
        (content.count(BROKER_DETAIL), f'title="{BROKER_DETAIL}"' in content),
        m_lost_air_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, True, True, False, False, (1, True), 1, 1)


def test_dashboard_shows_the_phase_currents_and_refreshes_them_on_their_own(
    f_russian_client: Client,
    m_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Ток по фазам' in content,
        ['12,4 А' in content, '3,1 А' in content, '27,8 А' in content],
        'hx-get="/phases/"' in content,
        m_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, [True, True, True], True, 1, 1)


def test_dashboard_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/')
