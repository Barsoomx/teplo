from collections.abc import Iterator
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_settings_page import GetSettingsPageInputDTO, GetSettingsPageOutputDTO

pytestmark = pytest.mark.django_db

GATE_INPUTS = (
    'name="live_control"',
    'name="air_control_live"',
    'name="second_floor_guard_live"',
    'name="load_management_live"',
    'name="loads-0-live"',
    'name="loads-1-live"',
)


@pytest.fixture
def m_settings_use_case(settings_page: GetSettingsPageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.settings_page.GetSettingsPageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = settings_page
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
        'name="second_floor_guard_threshold" value="19.0"' in content,
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
            'href="/admin/climate/climatesettings/1/change/"' in content,
            'href="/admin/power/loadsettings/1/change/"' in content,
            'href="/admin/power/load/1/change/"' in content,
            'href="/admin/power/load/2/change/"' in content,
        ],
        content.count('Изменить в админке'),
        content.count('class="chip gate-live"'),
        [gate for gate in GATE_INPUTS if gate in content],
        m_settings_use_case.init.call_count,
    ) == ([True] * 6, 7, 2, [], 1)


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
