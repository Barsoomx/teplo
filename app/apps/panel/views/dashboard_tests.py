from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.climate.domain.modes import ControlPath, ScheduleMode
from apps.climate.domain.schedule import ScheduleTransition
from apps.devices.domain.kinds import DeviceKind
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, SensorOverview, ZoneRegime
from apps.panel.usecases.get_phase_currents import GetPhaseCurrentsOutputDTO
from apps.power.domain.loads import LoadPhase
from apps.power.domain.outage import OutageCause, OutageRecord, PrecedingOverload
from apps.system.domain.clock import ClockStatus, ClockStatusCode

pytestmark = pytest.mark.django_db

RAW_DETAILS = ('Network Error: Device Unreachable', 'secret missing: thermostat-hallway', 'ndmc failed: timed out')
PLUG_DETAIL = 'unexpected plug payload: {}'
SENSOR_DETAIL = 'temperature: the node reported nan'
BROKER_DETAIL = 'mqtt connection refused: not authorised'
CUT = datetime(2026, 10, 31, 20, 24, 40, tzinfo=ZoneInfo('Europe/Moscow'))
COMFORT_AT = datetime(2026, 10, 29, 6, 30, tzinfo=ZoneInfo('Europe/Moscow'))
ANSWERS = ('overload', 'grid', 'unknown')


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
    kitchen = replace(panel_overview.zones[0], air=lost_air, path=ControlPath.FLOOR, air_target=None)
    return replace(panel_overview, zones=(kitchen,), outdoor=None, standalone_sensors=())


@pytest.fixture
def m_lost_air_overview_use_case(lost_air_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = lost_air_overview
        yield use_case


@pytest.fixture
def air_paths_overview(panel_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    kitchen = panel_overview.zones[0]
    living_room = replace(
        kitchen,
        slug='living-room',
        name='Гостиная',
        mode=ScheduleMode.PREHEAT,
        target_setpoint=Decimal('30.0'),
        air_control_live=True,
        path=ControlPath.AIR,
        regime=ZoneRegime.PREHEAT,
        lead=timedelta(hours=2, minutes=40),
        next_change=ScheduleTransition(mode=ScheduleMode.COMFORT, at=COMFORT_AT),
    )
    hallway = replace(
        kitchen,
        slug='hallway',
        name='Прихожая',
        path=ControlPath.FLOOR,
        air_target=None,
        air=SensorOverview(
            slug='air-hallway',
            name='Воздух: прихожая',
            kind=DeviceKind.TEMPERATURE_SENSOR,
            available=False,
            error_code='device_unavailable',
            error=None,
            temperature=None,
            humidity=None,
            age_seconds=None,
        ),
    )
    return replace(panel_overview, zones=(living_room, kitchen, hallway))


@pytest.fixture
def m_air_paths_overview_use_case(air_paths_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = air_paths_overview
        yield use_case


@pytest.fixture
def outage_overview(panel_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    return replace(
        panel_overview,
        outages=(
            OutageRecord(
                id=7,
                began_at=CUT,
                ended_at=CUT + timedelta(minutes=9),
                cause=OutageCause.UNMARKED,
                cause_marked_at=None,
            ),
        ),
    )


@pytest.fixture
def m_outage_overview_use_case(outage_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = outage_overview
        yield use_case


@pytest.fixture
def overloaded_outage_overview(outage_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    overload = PrecedingOverload(phase=LoadPhase.L2, amps=Decimal('26.4'))
    return replace(outage_overview, outages=(replace(outage_overview.outages[0], overload=overload),))


@pytest.fixture
def m_overloaded_outage_overview_use_case(
    overloaded_outage_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = overloaded_outage_overview
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
        'По полу' in content,
        (content.count(BROKER_DETAIL), f'title="{BROKER_DETAIL}"' in content),
        m_lost_air_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, True, True, False, False, True, (1, True), 1, 1)


def test_dashboard_names_the_path_the_air_target_and_the_lead_of_a_warm_up(
    f_russian_client: Client,
    m_air_paths_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        content.count('class="chip path path-air"'),
        content.count('По воздуху: наблюдение'),
        content.count('По полу'),
        content.count('Воздух, цель 22,0°'),
        'Комфорт с 06:30' in content,
        content.count('Разогрев начат за 2 ч 40 мин'),
        m_air_paths_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (1, 1, 1, 2, True, 1, 1, 1)


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


def test_dashboard_asks_what_an_unmarked_outage_was(
    f_russian_client: Client,
    m_outage_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Свет пропадал' in content,
        '20:24–20:33 (9 мин). Что это было?' in content,
        [content.count(f'value="{answer}" hx-post="/outages/7/cause/"') for answer in ANSWERS],
        ('Перегруз: выбило наш автомат' in content, 'Пропало в посёлке' in content, 'Не знаю' in content),
        '<fieldset class="answers" hx-target="#zones" hx-swap="innerHTML"' in content,
        m_outage_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, True, [1, 1, 1], (True, True, True), True, 1, 1)


def test_dashboard_puts_the_overload_answer_forward_when_an_overload_preceded_the_cut(
    f_russian_client: Client,
    m_overloaded_outage_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    overload_answer = content.index('value="overload"')
    assert (
        'Перед отключением фаза L2 была перегружена: 26,4 А' in content,
        'id="outage-7-overload"' in content,
        content.count('class="answer answer-suggested"'),
        'aria-describedby="outage-7-overload"'
        in content[overload_answer : content.index('</button>', overload_answer)],
        overload_answer < content.index('value="grid"') < content.index('value="unknown"'),
        'aria-pressed' in content,
        m_overloaded_outage_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, True, 1, True, True, False, 1, 1)


def test_dashboard_without_an_overload_before_the_cut_suggests_no_answer(
    f_russian_client: Client,
    m_outage_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Перед отключением' in content,
        'answer-suggested' in content,
        m_outage_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (False, False, 1, 1)


def test_dashboard_lists_the_loads_in_shedding_order_with_their_phase_current_and_state(
    f_russian_client: Client,
    m_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    shedding = content[content.index('class="shedding"') :]
    assert (
        'Очередь отключения' in content,
        shedding.count('class="shed-load"'),
        shedding.index('Бойлер 1 этаж') < shedding.index('Кухня'),
        ('Фаза L2, выучена' in shedding, '6,0 А, номинал' in shedding, 'Была бы отключена' in shedding),
        ('Фаза неизвестна' in shedding, '6,0 А по умолчанию' in shedding, 'Потребляет' in shedding),
        shedding.count('Наблюдение'),
        m_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, 2, True, (True, True, True), (True, True, True), 1, 1, 1)


def test_dashboard_without_unmarked_outages_asks_nothing(
    f_russian_client: Client,
    m_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Что это было?' in content,
        '/cause/' in content,
        m_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (False, False, 1, 1)


def test_dashboard_redirects_anonymous_to_login(f_anonymous_client: Client) -> None:
    response = f_anonymous_client.get('/')

    assert (response.status_code, response['Location']) == (302, '/login/?next=/')
