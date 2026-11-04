from collections.abc import Iterator
from dataclasses import replace
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.climate.domain.heat_sources import HeatSourceKind, SourceGate
from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageOutputDTO

pytestmark = pytest.mark.django_db

GATE_INPUTS = (
    'name="live_control"',
    'name="air_control_live"',
    'name="backup_live"',
    'name="manual_hold_live"',
    'name="load_management_live"',
    'name="loads-0-live"',
    'name="loads-1-live"',
)


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.settings_page.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
        yield use_case


@pytest.fixture
def m_manual_hold_settings_use_case(
    request: pytest.FixtureRequest,
    settings_page: GetSettingsPageOutputDTO,
) -> Iterator[MagicMock]:
    climate = replace(settings_page.climate, manual_hold_live=request.param)
    with mock.patch('apps.panel.views.settings_page.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(settings_page, climate=climate)
        yield use_case


@pytest.fixture
def m_fireplace_settings_use_case(
    request: pytest.FixtureRequest,
    settings_page: GetSettingsPageOutputDTO,
) -> Iterator[MagicMock]:
    bathroom, bedroom = settings_page.rooms
    open_space = replace(
        bathroom,
        slug='open-space',
        name='Гостиная, кухня и прихожая',
        sources=(SourceGate(id=7, name='Камин', kind=HeatSourceKind.FIREPLACE, live=request.param),),
    )
    with mock.patch('apps.panel.views.settings_page.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(settings_page, rooms=(open_space, bedroom))
        yield use_case


def test_settings_page_shows_the_settings_of_every_zone(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    assert (
        response.status_code,
        ['Кухня' in content, 'Санузел 1 этаж' in content],
        'hx-post="/settings/zones/kitchen/"' in content,
        'name="comfort_floor_setpoint" value="28.0"' in content,
        'name="preheat_lead_minutes" value="120"' in content,
        content.count('name="comfort_air_target"'),
        'id="zone-bathroom-1f-night_floor_setpoint"' in content,
        m_settings_use_case.init.return_value.execute.call_args.args,
    ) == (200, [True, True], True, True, True, 1, True, (GetSettingsPageInputDTO(),))


def test_settings_page_shows_climate_load_management_and_the_shedding_order(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    assert (
        'name="air_start_offset" value="0.30"' in content,
        'name="air_min_band" value="0.20"' in content,
        'name="second_floor_guard_threshold"' not in content,
        'name="shed_amps" value="24.0"' in content,
        'name="loads-TOTAL_FORMS" value="2"' in content,
        'name="loads-1-priority" value="2"' in content,
        '<option value="l2" selected>L2</option>' in content,
        'Очередь отключения' in content,
        m_settings_use_case.init.call_count,
    ) == (True, True, True, True, True, True, True, True, 1)


def test_settings_page_shows_every_gate_read_only_with_a_link_to_the_admin(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    assert (
        [
            'href="/admin/climate/zone/3/change/"' in content,
            'href="/admin/climate/zone/4/change/"' in content,
            'href="/admin/climate/roomclimate/6/change/"' in content,
            'href="/admin/power/loadsettings/1/change/"' in content,
            'href="/admin/power/load/1/change/"' in content,
            'href="/admin/power/load/2/change/"' in content,
            'href="/admin/climate/climatesettings/1/change/"' in content,
        ],
        content.count('Изменить в админке'),
        content.count('class="chip gate-live"'),
        [gate for gate in GATE_INPUTS if gate in content],
        m_settings_use_case.init.call_count,
    ) == ([True] * 7, 8, 2, [], 1)


def test_settings_page_says_how_changes_reach_the_devices_and_that_teplo_only_watches(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    assert (
        'термостат получает новую уставку при следующей разрешённой записи своей зоны' in content,
        'Режим наблюдения: teplo запущен с TEPLO_DRY_RUN' in content,
        'href="/settings/" aria-current="page"' in content,
        m_settings_use_case.init.call_count,
    ) == (True, True, True, 1)


def test_settings_page_requires_login(f_anonymous_client: Client, m_settings_use_case: MagicMock) -> None:
    response = f_anonymous_client.get('/settings/')

    assert (response.status_code, response['Location'], m_settings_use_case.init.call_count) == (
        302,
        '/login/?next=/settings/',
        0,
    )


def test_settings_page_lists_each_room_with_the_cards_of_its_zones_and_then_the_zones_in_no_room(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    places = [
        content.find(marker)
        for marker in (
            'id="rooms-title">Комнаты</h2>',
            'id="room-settings-bathroom-1f"',
            'id="zone-settings-bathroom-1f"',
            'id="room-settings-bedroom"',
            'id="zones-title">Зоны</h2>',
            'id="zone-settings-kitchen"',
            'id="climate-settings"',
        )
    ]
    assert (
        -1 in places,
        places == sorted(places),
        'hx-post="/settings/rooms/bedroom/"' in content,
        'Спальня <small class="settings-floor">2 этаж</small>' in content,
        content.count('name="comfort_target"'),
        'name="setback_target" value="19.5"' in content,
        content.count('name="backup_below"'),
        'Резервный обогрев от пола: Санузел 2 этаж, °C' in content,
        content.count('Резервный обогрев'),
        m_settings_use_case.init.call_count,
    ) == (False, True, True, True, 2, True, 1, True, 2, 1)


def test_settings_page_marks_the_guest_rooms_and_sets_the_hour_guests_leave(
    f_russian_client: Client,
    m_settings_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    assert (
        content.count('name="guest_room"'),
        content.count('Гостевая комната'),
        'name="guests_end_hour" value="14"' in content,
        'Гости до, час' in content,
        'name="guests_until"' in content,
        m_settings_use_case.init.call_count,
    ) == (2, 2, True, True, False, 1)


@pytest.mark.parametrize(
    ('m_fireplace_settings_use_case', 'state'),
    [(False, 'Наблюдение'), (True, 'Работает')],
    ids=['watching', 'live'],
    indirect=['m_fireplace_settings_use_case'],
)
def test_settings_page_shows_the_gate_of_the_fireplace_detector_on_its_room(
    f_russian_client: Client,
    m_fireplace_settings_use_case: MagicMock,
    state: str,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    card_at = content.index('id="room-settings-open-space"')
    card = content[card_at : content.index('</article>', card_at)]
    gate_at = card.index('Детектор камина')
    assert (
        state in card[gate_at : card.index('</p>', gate_at)],
        'href="/admin/climate/heatsource/7/change/"' in card,
        m_fireplace_settings_use_case.init.call_count,
    ) == (True, True, 1)


@pytest.mark.parametrize(
    ('m_manual_hold_settings_use_case', 'state'),
    [(False, 'Наблюдение'), (True, 'Работает')],
    ids=['watching', 'live'],
    indirect=['m_manual_hold_settings_use_case'],
)
def test_settings_page_shows_the_gate_of_manual_changes_on_the_climate_card(
    f_russian_client: Client,
    m_manual_hold_settings_use_case: MagicMock,
    state: str,
) -> None:
    response = f_russian_client.get('/settings/')

    content = response.content.decode()
    card_at = content.index('id="climate-settings"')
    card = content[card_at : content.index('</article>', card_at)]
    gate_at = card.index('Ручная правка на термостатах')
    assert (
        state in card[gate_at : card.index('</p>', gate_at)],
        'href="/admin/climate/climatesettings/1/change/"' in card,
        m_manual_hold_settings_use_case.init.call_count,
    ) == (True, True, 1)
