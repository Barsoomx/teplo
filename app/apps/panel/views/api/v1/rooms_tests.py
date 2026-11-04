from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.zone_intent import ForcedTarget
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, RoomComfortOverview

pytestmark = pytest.mark.django_db

MANUAL_UNTIL = datetime(2026, 11, 4, 18, 40, tzinfo=ZoneInfo('Europe/Moscow'))
WITHOUT_ROOM_CLIMATE = {
    'comfort_target': None,
    'setback_target': None,
    'air_sensor': None,
    'backup_zone': None,
    'backup_live': False,
    'backup_active': False,
    'reason': None,
    'target': None,
    'until': None,
    'guest_room': False,
    'adjustable': False,
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
    open_space_intent = RoomIntent(
        room='open-space',
        reason=IntentReason.OVERRIDE,
        target=Decimal('22.5'),
        until=MANUAL_UNTIL,
        forced=(ForcedTarget(target=Decimal('22.5'), beneath=Decimal('22.0')),),
    )
    bedroom_intent = RoomIntent(
        room='bedroom',
        reason=IntentReason.SETBACK,
        target=Decimal('19.5'),
        until=None,
        forced=(),
    )
    return replace(
        house_overview,
        floors=(
            replace(
                ground,
                rooms=(
                    replace(
                        open_space,
                        comfort=open_space_comfort,
                        intent=open_space_intent,
                        adjustable=True,
                        guest_room=True,
                    ),
                ),
            ),
            replace(upstairs, rooms=(replace(bedroom, comfort=bedroom_comfort, intent=bedroom_intent), office)),
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


def test_rooms_api_gives_each_room_its_targets_its_backup_heat_and_what_it_holds_now(
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
                'reason': 'override',
                'target': '22.5',
                'until': '2026-11-04T18:40:00+03:00',
                'guest_room': True,
                'adjustable': True,
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
                'reason': 'setback',
                'target': '19.5',
                'until': None,
                'guest_room': False,
                'adjustable': False,
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
