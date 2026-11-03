from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client

from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, RoomComfortOverview

pytestmark = pytest.mark.django_db

WITHOUT_ROOM_CLIMATE = {
    'comfort_target': None,
    'setback_target': None,
    'air_sensor': None,
    'backup_zone': None,
    'backup_live': False,
    'backup_active': False,
}


@pytest.fixture
def comfort_overview(house_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    ground, upstairs = house_overview.floors
    [open_space] = ground.rooms
    bedroom, office = upstairs.rooms
    open_space_comfort = RoomComfortOverview(
        comfort_target=Decimal('22.0'),
        setback_target=Decimal('19.5'),
        air_sensor=None,
        backup_zone=None,
        backup_zone_name=None,
        backup_below=Decimal('19.0'),
        backup_live=False,
        backup_active=False,
    )
    bedroom_comfort = replace(
        open_space_comfort,
        comfort_target=Decimal('20.5'),
        air_sensor='air-second-floor',
        backup_zone='bathroom-2f',
        backup_zone_name='Санузел 2 этаж',
        backup_active=True,
    )
    return replace(
        house_overview,
        floors=(
            replace(ground, rooms=(replace(open_space, comfort=open_space_comfort),)),
            replace(upstairs, rooms=(replace(bedroom, comfort=bedroom_comfort), office)),
        ),
    )


@pytest.fixture
def m_overview_use_case(house_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.rooms.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = house_overview
        yield use_case


@pytest.fixture
def m_comfort_overview_use_case(comfort_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.rooms.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = comfort_overview
        yield use_case


def test_rooms_api_returns_rooms_in_floor_order(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/rooms/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'open-space',
                'name': 'Гостиная, кухня и прихожая',
                'floor': 'floor-1',
                'zones': ['kitchen'],
                'devices': ['air-kitchen', 'plug-boiler-1f', 'thermostat-kitchen'],
                **WITHOUT_ROOM_CLIMATE,
            },
            {
                'slug': 'bedroom',
                'name': 'Спальня',
                'floor': 'floor-2',
                'zones': [],
                'devices': ['air-second-floor'],
                **WITHOUT_ROOM_CLIMATE,
            },
            {
                'slug': 'office',
                'name': 'Кабинет',
                'floor': 'floor-2',
                'zones': [],
                'devices': [],
                **WITHOUT_ROOM_CLIMATE,
            },
        ],
        1,
    )


def test_rooms_api_gives_each_room_its_targets_and_its_backup_heat(
    f_client: Client,
    m_comfort_overview_use_case: MagicMock,
) -> None:
    response = f_client.get('/api/v1/rooms/')

    assert (response.json(), m_comfort_overview_use_case.init.call_count) == (
        [
            {
                'slug': 'open-space',
                'name': 'Гостиная, кухня и прихожая',
                'floor': 'floor-1',
                'zones': ['kitchen'],
                'devices': ['air-kitchen', 'plug-boiler-1f', 'thermostat-kitchen'],
                'comfort_target': '22.0',
                'setback_target': '19.5',
                'air_sensor': None,
                'backup_zone': None,
                'backup_live': False,
                'backup_active': False,
            },
            {
                'slug': 'bedroom',
                'name': 'Спальня',
                'floor': 'floor-2',
                'zones': [],
                'devices': ['air-second-floor'],
                'comfort_target': '20.5',
                'setback_target': '19.5',
                'air_sensor': 'air-second-floor',
                'backup_zone': 'bathroom-2f',
                'backup_live': False,
                'backup_active': True,
            },
            {
                'slug': 'office',
                'name': 'Кабинет',
                'floor': 'floor-2',
                'zones': [],
                'devices': [],
                **WITHOUT_ROOM_CLIMATE,
            },
        ],
        1,
    )


def test_rooms_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/rooms/').status_code == 403
