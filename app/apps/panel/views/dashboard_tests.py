from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from freezegun import freeze_time

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.domain.modes import ControlPath, ScheduleMode
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.schedule import ScheduleTransition
from apps.climate.domain.zone_intent import ForcedTarget
from apps.devices.domain.kinds import DeviceKind
from apps.panel.usecases.get_panel_overview import (
    GetPanelOverviewOutputDTO,
    RoomComfortOverview,
    SensorOverview,
    SourceOverview,
    ZoneRegime,
)
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
MOSCOW = ZoneInfo('Europe/Moscow')
PANEL_NOW = datetime(2026, 11, 4, 16, 40, tzinfo=MOSCOW)
GUESTS_LEAVE = datetime(2026, 11, 5, 14, 0, tzinfo=MOSCOW)
OPEN_SPACE = 'Гостиная, кухня и прихожая'
SYNCED = ClockStatus(trusted=True, code=ClockStatusCode.NTP_SYNCHRONIZED, reason='ntp synchronized')
UNSYNCED = ClockStatus(trusted=False, code=ClockStatusCode.NTP_NOT_SYNCHRONIZED, reason='ntp not synchronized')


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
        unplaced_zones=(kitchen, hallway),
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
    return replace(panel_overview, zones=(kitchen,), unplaced_zones=(kitchen,), outdoor=None, standalone_sensors=())


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
    zones = (living_room, kitchen, hallway)
    return replace(panel_overview, zones=zones, unplaced_zones=zones)


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
def m_house_overview_use_case(house_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = house_overview
        yield use_case


@pytest.fixture
def m_backup_overview_use_case(
    request: pytest.FixtureRequest,
    house_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    active, live = request.param
    comfort = RoomComfortOverview(
        comfort_target=Decimal('20.5'),
        setback_target=Decimal('19.5'),
        air_sensor='air-second-floor',
        backup_zone='bathroom-2f',
        backup_zone_name='Санузел 2 этаж',
        backup_below=Decimal('19.0'),
        backup_live=live,
        backup_active=active,
    )
    ground, upstairs = house_overview.floors
    bedroom, office = upstairs.rooms
    floors = (ground, replace(upstairs, rooms=(replace(bedroom, comfort=comfort), office)))
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(house_overview, floors=floors, dry_run=False)
        yield use_case


@pytest.fixture
def m_unplaced_overview_use_case(
    request: pytest.FixtureRequest,
    panel_overview: GetPanelOverviewOutputDTO,
    house_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    lost_boiler = panel_overview.plugs[1]
    overviews = {
        'with_floors': replace(
            house_overview,
            plugs=(*house_overview.plugs, lost_boiler),
            unplaced_plugs=(lost_boiler,),
        ),
        'without_floors': panel_overview,
    }
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = overviews[request.param]
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


@pytest.fixture
def m_guests_overview_use_case(
    request: pytest.FixtureRequest,
    house_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    guest_rooms, guests_until, clock = request.param
    overview = replace(house_overview, guest_rooms=guest_rooms, guests_until=guests_until, clock=clock)
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = overview
        yield use_case


@pytest.fixture
def m_intent_overview_use_case(
    request: pytest.FixtureRequest,
    house_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    reason, target, until, adjustable = request.param
    forced = (ForcedTarget(target=target, beneath=Decimal('19.5')),)
    intent = RoomIntent(
        room='open-space',
        reason=reason,
        target=target,
        until=until,
        forced=forced if reason in (IntentReason.OVERRIDE, IntentReason.GUESTS) else (),
    )
    ground, upstairs = house_overview.floors
    [open_space] = ground.rooms
    bedroom, office = upstairs.rooms
    bedroom_intent = RoomIntent(
        room='bedroom',
        reason=IntentReason.SETBACK,
        target=Decimal('19.5'),
        until=None,
        forced=(),
    )
    floors = (
        replace(ground, rooms=(replace(open_space, intent=intent, adjustable=adjustable, guest_room=True),)),
        replace(upstairs, rooms=(replace(bedroom, intent=bedroom_intent), office)),
    )
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(house_overview, floors=floors)
        yield use_case


@pytest.fixture
def m_fireplace_overview_use_case(
    request: pytest.FixtureRequest,
    house_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    lit_until, burning, live = request.param
    fireplace = SourceOverview(
        slug='fireplace',
        name='Камин',
        kind=HeatSourceKind.FIREPLACE,
        live=live,
        lit_until=lit_until,
        burning=burning,
    )
    ground, upstairs = house_overview.floors
    [open_space] = ground.rooms
    floors = (replace(ground, rooms=(replace(open_space, sources=(fireplace,)),)), upstairs)
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(house_overview, floors=floors)
        yield use_case


@pytest.fixture
def m_pending_overview_use_case(
    request: pytest.FixtureRequest,
    panel_overview: GetPanelOverviewOutputDTO,
) -> Iterator[MagicMock]:
    power, pending = request.param
    kitchen = replace(panel_overview.zones[0], power=power, target_setpoint=Decimal('30.0'), pending=pending)
    with mock.patch('apps.panel.views.dashboard.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(
            panel_overview,
            zones=(kitchen,),
            unplaced_zones=(kitchen,),
        )
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


def test_dashboard_groups_rooms_under_their_floors(
    f_russian_client: Client,
    m_house_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    places = [
        content.find(marker)
        for marker in (
            'id="floor-floor-1">1 этаж</h2>',
            'id="room-open-space">Гостиная, кухня и прихожая</h3>',
            '<h4 class="tile-name">Кухня</h4>',
            '<h4 class="load-name">Бойлер 1 этаж</h4>',
            'id="floor-floor-2">2 этаж</h2>',
            'id="room-bedroom">Спальня</h3>',
            '<h4 class="tile-name">Воздух: спальня</h4>',
            'id="loads-title"',
        )
    ]
    assert (
        -1 in places,
        places == sorted(places),
        content.count('class="floor"'),
        content.count('class="room"'),
        m_house_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (False, True, 2, 2, 1, 1)


def test_dashboard_shows_the_air_of_a_room_without_a_zone_in_large_type(
    f_russian_client: Client,
    m_house_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    bedroom_at = content.index('id="room-bedroom"')
    bedroom = content[bedroom_at : content.index('</section>', bedroom_at)]
    assert (
        bedroom.count('<article class="tile tile-air">'),
        '<p class="readout-value">19,8<span class="readout-unit">°</span> <span class="air-humidity">' in bedroom,
        '38%' in bedroom,
        '<p class="readout-caption">Воздух</p>' in bedroom,
        ('style=' in bedroom, 'tile-floor' in bedroom, 'scale' in bedroom),
        ('air-row' in content, content.count('Воздух: спальня')),
        m_house_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (1, True, True, True, (False, False, False), (False, 1), 1, 1)


@pytest.mark.parametrize(
    ('m_backup_overview_use_case', 'chip', 'watching', 'caption'),
    [((True, False), True, True, False), ((True, True), True, False, False), ((False, False), False, False, True)],
    ids=['on_while_watching', 'on_and_live', 'off'],
    indirect=['m_backup_overview_use_case'],
)
def test_dashboard_names_the_backup_heat_of_a_room_while_it_is_on_and_its_threshold_while_it_is_off(
    f_russian_client: Client,
    m_backup_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    chip: bool,
    watching: bool,
    caption: bool,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    bedroom_at = content.index('id="room-bedroom"')
    bedroom = content[bedroom_at : content.index('</header>', bedroom_at)]
    assert (
        'Резерв: Санузел 2 этаж' in bedroom,
        'Наблюдение' in bedroom,
        'Резерв ниже 19,0°: Санузел 2 этаж' in bedroom,
        m_backup_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (chip, watching, caption, 1, 1)


def test_dashboard_leaves_out_a_room_with_nothing_to_show(
    f_russian_client: Client,
    m_house_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Кабинет' in content,
        'room-office' in content,
        'id="room-bedroom"' in content,
        m_house_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (False, False, True, 1, 1)


@pytest.mark.parametrize(
    ('m_unplaced_overview_use_case', 'named'),
    [('with_floors', True), ('without_floors', False)],
    indirect=['m_unplaced_overview_use_case'],
)
def test_dashboard_names_zones_and_plugs_without_a_room_only_when_the_house_has_floors(
    f_russian_client: Client,
    m_unplaced_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    named: bool,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'Без комнаты' in content,
        'id="floor-unplaced"' in content,
        '<h3 class="load-name">Бойлер 2 этаж</h3>' in content,
        m_unplaced_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (named, named, True, 1, 1)


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


@freeze_time(PANEL_NOW)
@pytest.mark.parametrize(
    ('m_guests_overview_use_case', 'texts'),
    [
        (
            ((OPEN_SPACE,), None, SYNCED),
            (True, 'title="Гостевые комнаты: Гостиная, кухня и прихожая"', 'Гости на<', 'hx-vals=\'{"nights": 3}\''),
        ),
        (
            ((OPEN_SPACE,), GUESTS_LEAVE, SYNCED),
            (True, 'Гости до 14:00 завтра', 'hx-vals=\'{"nights": 0}\'', 'hx-disabled-elt="this">2 ночи<'),
        ),
        (
            ((OPEN_SPACE,), None, UNSYNCED),
            (
                True,
                'title="Гостей можно включить, когда часы синхронизируются"',
                'hx-disabled-elt="this" disabled>1 ночь<',
                'hx-disabled-elt="this" disabled>3 ночи<',
            ),
        ),
        (((), None, SYNCED), (False, 'Гостевые комнаты', 'Гости на<', '/guests/')),
    ],
    ids=['offered', 'staying', 'waiting_for_the_clock', 'no_guest_room'],
    indirect=['m_guests_overview_use_case'],
)
def test_dashboard_switches_guests_on_and_off_in_the_house_facts(
    f_russian_client: Client,
    m_guests_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    texts: tuple[bool, str, str, str],
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    shown, *markers = texts
    facts = content[content.index('class="house"') : content.index('</ul>', content.index('class="house"'))]
    assert (
        'fact fact-guests' in facts,
        [marker in facts for marker in markers],
        m_guests_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (shown, [shown] * len(markers), 1, 1)


@freeze_time(PANEL_NOW)
@pytest.mark.parametrize(
    ('m_intent_overview_use_case', 'chip', 'back'),
    [
        (
            (IntentReason.OVERRIDE, Decimal('22.5'), PANEL_NOW + timedelta(hours=2), True),
            'Вручную 22,5° до 18:40',
            True,
        ),
        ((IntentReason.GUESTS, Decimal('22.0'), GUESTS_LEAVE, True), 'Гости до 14:00 завтра', False),
        ((IntentReason.COMFORT, Decimal('22.0'), PANEL_NOW + timedelta(hours=7), True), None, False),
    ],
    ids=['manual', 'guests', 'schedule'],
    indirect=['m_intent_overview_use_case'],
)
def test_dashboard_names_why_a_room_holds_its_target_and_offers_the_schedule_back_from_a_manual_one(
    f_russian_client: Client,
    m_intent_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    chip: str | None,
    back: bool,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    open_space_at = content.index('id="room-open-space"')
    header = content[open_space_at : content.index('</header>', open_space_at)]
    room = content[open_space_at : content.index('</section>', open_space_at)]
    assert (
        'class="chip intent"' in header,
        chip is None or chip in header,
        'Вернуть расписание' in room,
        'hx-post="/rooms/open-space/override/clear/"' in room,
        m_intent_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (chip is not None, True, back, back, 1, 1)


@freeze_time(PANEL_NOW)
@pytest.mark.parametrize(
    ('m_intent_overview_use_case', 'target', 'colder', 'warmer'),
    [
        ((IntentReason.COMFORT, Decimal('22.0'), None, True), 'Цель 22,0°', True, True),
        ((IntentReason.OVERRIDE, Decimal('10.0'), PANEL_NOW + timedelta(hours=1), True), 'Цель 10,0°', False, True),
        ((IntentReason.OVERRIDE, Decimal('30.0'), PANEL_NOW + timedelta(hours=1), True), 'Цель 30,0°', True, False),
    ],
    ids=['both_ways', 'at_the_lowest', 'at_the_highest'],
    indirect=['m_intent_overview_use_case'],
)
def test_dashboard_steps_an_adjustable_room_by_half_a_degree_within_its_range(
    f_russian_client: Client,
    m_intent_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    target: str,
    colder: bool,
    warmer: bool,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    row_at = content.index('class="room-target"')
    row = content[row_at : content.index('</div>', row_at)]
    colder_button = row[row.index('aria-label="Холоднее на 0,5°"') : row.index('>−<')]
    warmer_button = row[row.index('aria-label="Теплее на 0,5°"') : row.index('>+<')]
    assert (
        target in row,
        'hx-post="/rooms/open-space/override/"' in row,
        ('hx-vals=\'{"step": "down"}\'' in colder_button, ' disabled' not in colder_button),
        ('hx-vals=\'{"step": "up"}\'' in warmer_button, ' disabled' not in warmer_button),
        content.count('class="room-target"'),
        m_intent_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, True, (True, colder), (True, warmer), 1, 1, 1)


@pytest.mark.parametrize(
    'm_intent_overview_use_case',
    [(IntentReason.COMFORT, Decimal('22.0'), None, False)],
    ids=['open_space_without_an_air_reading'],
    indirect=True,
)
def test_dashboard_offers_no_steps_in_a_room_that_takes_no_override(
    f_russian_client: Client,
    m_intent_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    assert (
        'room-target' in content,
        'Холоднее' in content,
        m_intent_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (False, False, 1, 1)


@freeze_time(PANEL_NOW)
@pytest.mark.parametrize(
    ('m_pending_overview_use_case', 'caption'),
    [
        (
            (True, PendingWrite(kind=PendingKind.NEXT_WRITE, at=PANEL_NOW + timedelta(minutes=12))),
            'Пол, цель 30°. На термостате 28°, 30° — не раньше 16:52',
        ),
        (
            (True, PendingWrite(kind=PendingKind.NEXT_WRITE, at=PANEL_NOW)),
            'Пол, цель 30°. На термостате 28°, 30° — в ближайшую минуту',
        ),
        ((False, PendingWrite(kind=PendingKind.HELD, at=None)), 'Пол, цель 30°. Пол отключён управлением нагрузкой'),
        ((False, PendingWrite(kind=PendingKind.NEXT_WRITE, at=PANEL_NOW)), 'Пол, цель 30°. Термостат выключен'),
        ((True, PendingWrite(kind=PendingKind.WATCHING, at=None)), 'Пол, цель 30°. На термостате 28°'),
    ],
    ids=['next_write_later', 'next_write_now', 'held_by_load_management', 'switched_off', 'watching'],
    indirect=['m_pending_overview_use_case'],
)
def test_dashboard_says_when_a_floor_gets_its_target(
    f_russian_client: Client,
    m_pending_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    caption: str,
) -> None:
    response = f_russian_client.get('/')

    assert (
        f'<p class="readout-caption">{caption}</p>' in response.content.decode(),
        m_pending_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, 1, 1)


@freeze_time(PANEL_NOW)
@pytest.mark.parametrize(
    ('m_fireplace_overview_use_case', 'lit_chip', 'detected', 'watching', 'button'),
    [
        ((None, False, False), None, False, False, ('Топлю камин', '{"lit": "1"}')),
        (
            (PANEL_NOW + timedelta(hours=4), False, False),
            'Камин топится до 20:40',
            False,
            False,
            ('Камин погас', '{"lit": "0"}'),
        ),
        (
            (PANEL_NOW + timedelta(hours=8), False, False),
            'Камин топится до 00:40 завтра',
            False,
            False,
            ('Камин погас', '{"lit": "0"}'),
        ),
        ((None, True, False), None, True, True, ('Топлю камин', '{"lit": "1"}')),
        ((None, True, True), None, True, False, ('Топлю камин', '{"lit": "1"}')),
    ],
    ids=['unmarked', 'marked', 'marked_past_midnight', 'detected_while_watching', 'detected_live'],
    indirect=['m_fireplace_overview_use_case'],
)
def test_dashboard_marks_the_fireplace_and_shows_what_the_detector_sees(
    f_russian_client: Client,
    m_fireplace_overview_use_case: MagicMock,
    m_phases_use_case: MagicMock,
    lit_chip: str | None,
    detected: bool,
    watching: bool,
    button: tuple[str, str],
) -> None:
    response = f_russian_client.get('/')

    content = response.content.decode()
    row_at = content.index('class="room-source"')
    row = content[row_at : content.index('</div>', row_at)]
    label, posted = button
    assert (
        lit_chip is None or lit_chip in row,
        'Камин топится до' in row,
        'Похоже, топится камин' in row,
        'Наблюдение' in row,
        (label in row, f"hx-vals='{posted}'" in row, 'hx-post="/rooms/open-space/fireplace/"' in row),
        m_fireplace_overview_use_case.init.call_count,
        m_phases_use_case.init.call_count,
    ) == (True, lit_chip is not None, detected, watching, (True, True, True), 1, 1)
