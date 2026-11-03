from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_schedule_page import GetSchedulePageInputDTO, GetSchedulePageOutputDTO, ZoneWarmUp

pytestmark = pytest.mark.django_db

WARM_UP = 'Разогрев начинается за 3 ч до комфорта.'
LEARNED_START = (
    'Режим по воздуху начинает разогрев по выученному, не раньше чем за 4 ч до комфорта. '
    'Пока выученных утр меньше 3, разогрев начинается за 3 ч до комфорта.'
)
ZONE_LEARNED_START = (
    'Кухня: режим по воздуху начинает разогрев по выученному, не раньше чем за 4 ч до комфорта; '
    'пока выученных утр меньше 3 — за 3 ч.'
)


@pytest.fixture
def rooms_schedule_page(schedule_page: GetSchedulePageOutputDTO) -> GetSchedulePageOutputDTO:
    kitchen, living_room = schedule_page.rooms
    open_space = replace(
        kitchen,
        slug='open-space',
        name='Гостиная, кухня и прихожая',
        preheat_lead=timedelta(hours=4),
        zones=(
            ZoneWarmUp(slug='living-room', name='Гостиная', preheat_lead=timedelta(minutes=150), optimal_start=False),
            ZoneWarmUp(slug='kitchen', name='Кухня', preheat_lead=timedelta(hours=3), optimal_start=True),
            ZoneWarmUp(slug='hallway', name='Прихожая', preheat_lead=timedelta(hours=4), optimal_start=False),
        ),
    )
    bedroom = replace(living_room, slug='bedroom', name='Спальня', floor_name='2 этаж', zones=())
    return replace(schedule_page, rooms=(open_space, bedroom))


@pytest.fixture
def m_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.schedule_page.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = schedule_page
        yield use_case


@pytest.fixture
def m_rooms_schedule_use_case(rooms_schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.schedule_page.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = rooms_schedule_page
        yield use_case


@pytest.fixture
def m_empty_schedule_use_case(schedule_page: GetSchedulePageOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.schedule_page.GetSchedulePageUseCase') as use_case:
        use_case.init.return_value.execute.return_value = replace(schedule_page, rooms=())
        yield use_case


def test_schedule_page_shows_each_day_with_its_windows_and_periods(
    f_russian_client: Client,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/schedule/')

    content = response.content.decode()
    assert (
        response.status_code,
        ['Понедельник' in content, 'Воскресенье' in content],
        ['Ночь 00:00–03:30' in content, 'Разогрев 03:30–06:30' in content, 'Комфорт 06:30–23:30' in content],
        'Ночь 23:30–24:00' in content,
        content.count('Без комфорта: весь день ночь'),
        'style="--from: 390; --to: 1410;"' in content,
        m_schedule_use_case.init.return_value.execute.call_args.args,
    ) == (200, [True, True], [True, True, True], True, 13, True, (GetSchedulePageInputDTO(),))


def test_schedule_page_says_when_the_warm_up_starts_and_when_a_change_reaches_the_thermostat(
    f_russian_client: Client,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/schedule/')

    content = response.content.decode()
    assert (
        WARM_UP in content,
        LEARNED_START in content,
        'Термостат получает новую цель при следующей разрешённой записи своей зоны' in content,
        m_schedule_use_case.init.call_count,
    ) == (True, True, True, 1)


def test_schedule_page_offers_to_edit_delete_add_and_copy_windows(
    f_client: Client,
    m_schedule_use_case: MagicMock,
) -> None:
    response = f_client.get('/schedule/')

    content = response.content.decode()
    assert (
        'hx-post="/schedule/windows/1/"' in content,
        'hx-post="/schedule/windows/1/delete/"' in content,
        'hx-confirm="Delete the comfort window 06:30–23:30?"' in content,
        content.count('hx-post="/schedule/kitchen/days/'),
        'hx-post="/schedule/kitchen/days/0/copy/"' in content,
        'hx-post="/schedule/kitchen/copy/"' in content,
        'name="rooms" value="living-room"' in content,
        'step="900"' in content,
        m_schedule_use_case.init.call_count,
    ) == (True, True, True, 14, True, True, True, True, 1)


def test_navigation_marks_the_schedule_page(f_client: Client, m_schedule_use_case: MagicMock) -> None:
    response = f_client.get('/schedule/')

    content = response.content.decode()
    assert (
        'href="/schedule/" aria-current="page"' in content,
        'href="/settings/"' in content,
        'href="/journal/"' in content,
        m_schedule_use_case.init.call_count,
    ) == (True, True, True, 1)


def test_schedule_page_requires_login(f_anonymous_client: Client, m_schedule_use_case: MagicMock) -> None:
    response = f_anonymous_client.get('/schedule/')

    assert (response.status_code, response['Location'], m_schedule_use_case.init.call_count) == (
        302,
        '/login/?next=/schedule/',
        0,
    )


def test_schedule_page_gives_a_room_of_several_zones_the_warm_up_of_each_and_says_when_a_room_has_nothing_to_heat(
    f_russian_client: Client,
    m_rooms_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/schedule/')

    content = response.content.decode()
    assert (
        content.index('id="schedule-open-space"') < content.index('id="schedule-bedroom"'),
        'Гостиная, кухня и прихожая <small class="schedule-floor">1 этаж</small>' in content,
        'Гостиная: разогрев начинается за 2 ч 30 мин до комфорта.' in content,
        ZONE_LEARNED_START in content,
        'Прихожая: разогрев начинается за 4 ч до комфорта.' in content,
        'teplo пока нечем греть эту комнату по расписанию.' in content,
        (WARM_UP in content, LEARNED_START in content),
        'Копировать неделю в другие комнаты' in content,
        m_rooms_schedule_use_case.init.call_count,
    ) == (True, True, True, True, True, True, (False, False), True, 1)


def test_schedule_page_without_rooms_says_where_they_come_from(
    f_russian_client: Client,
    m_empty_schedule_use_case: MagicMock,
) -> None:
    response = f_russian_client.get('/schedule/')

    content = response.content.decode()
    assert (
        'Комнат пока нет. Они задаются данными сайта.' in content,
        'schedule-room"' in content,
        m_empty_schedule_use_case.init.call_count,
    ) == (True, False, 1)
