from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from unittest import mock
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from apps.climate.domain.drive import PendingKind, PendingWrite
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.devices.domain.readings import ClimateMode
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO, SourceOverview

pytestmark = pytest.mark.django_db

LIT_UNTIL = datetime(2026, 11, 4, 22, 10, tzinfo=ZoneInfo('Europe/Moscow'))
HELD_UNTIL = datetime(2026, 11, 4, 18, 40, tzinfo=ZoneInfo('Europe/Moscow'))


@pytest.fixture
def sources_overview(panel_overview: GetPanelOverviewOutputDTO) -> GetPanelOverviewOutputDTO:
    fireplace = SourceOverview(
        slug='fireplace',
        name='Камин',
        kind=HeatSourceKind.FIREPLACE,
        live=False,
        lit_until=LIT_UNTIL,
        burning=True,
        room='open-space',
        priority=1,
    )
    convector = SourceOverview(
        slug='convector-office',
        name='Конвектор: кабинет',
        kind=HeatSourceKind.HEATER,
        live=True,
        lit_until=None,
        burning=False,
        room='office',
        device='heater-office',
        priority=1,
        available=True,
        error_code=None,
        error=None,
        on=True,
        setpoint=Decimal(25),
        level='High',
        temperature=Decimal(21),
        target_on=True,
        target_setpoint=Decimal(22),
        commands_live=True,
        held_until=HELD_UNTIL,
        shed=False,
        pending=PendingWrite(kind=PendingKind.HANDS_OFF, at=HELD_UNTIL),
        mode=ClimateMode.HEAT,
    )
    air_conditioner = SourceOverview(
        slug='ac-bedroom',
        name='Кондиционер: спальня',
        kind=HeatSourceKind.AIR_CONDITIONER,
        live=False,
        lit_until=None,
        burning=False,
        room='bedroom',
        device='ac-bedroom',
        priority=1,
        available=True,
        on=True,
        setpoint=Decimal('24.0'),
        temperature=Decimal('18.5'),
        target_on=False,
        mode=ClimateMode.COOL,
        locked_out=True,
        outdoor=Decimal('-17.0'),
    )
    return replace(panel_overview, sources=(fireplace, convector, air_conditioner))


@pytest.fixture
def m_overview_use_case(sources_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.api.v1.sources.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = sources_overview
        yield use_case


def test_sources_api_lists_every_heat_source_in_room_order(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.get('/api/v1/sources/')

    assert (response.status_code, response.json(), m_overview_use_case.init.call_count) == (
        200,
        [
            {
                'slug': 'fireplace',
                'name': 'Камин',
                'room': 'open-space',
                'kind': 'fireplace',
                'device': None,
                'priority': 1,
                'live': False,
                'available': False,
                'on': None,
                'mode': None,
                'setpoint': None,
                'level': None,
                'temperature': None,
                'target_setpoint': None,
                'manual_until': None,
                'shed': False,
                'locked_out': False,
                'lit_until': '2026-11-04T22:10:00+03:00',
                'burning': True,
            },
            {
                'slug': 'convector-office',
                'name': 'Конвектор: кабинет',
                'room': 'office',
                'kind': 'heater',
                'device': 'heater-office',
                'priority': 1,
                'live': True,
                'available': True,
                'on': True,
                'mode': 'heat',
                'setpoint': '25.0',
                'level': 'High',
                'temperature': '21.0',
                'target_setpoint': '22.0',
                'manual_until': '2026-11-04T18:40:00+03:00',
                'shed': False,
                'locked_out': False,
                'lit_until': None,
                'burning': False,
            },
            {
                'slug': 'ac-bedroom',
                'name': 'Кондиционер: спальня',
                'room': 'bedroom',
                'kind': 'air_conditioner',
                'device': 'ac-bedroom',
                'priority': 1,
                'live': False,
                'available': True,
                'on': True,
                'mode': 'cool',
                'setpoint': '24.0',
                'level': None,
                'temperature': '18.5',
                'target_setpoint': None,
                'manual_until': None,
                'shed': False,
                'locked_out': True,
                'lit_until': None,
                'burning': False,
            },
        ],
        1,
    )


def test_sources_api_is_read_only(f_client: Client, m_overview_use_case: MagicMock) -> None:
    response = f_client.post('/api/v1/sources/', {'live': True})

    assert (response.status_code, m_overview_use_case.init.call_count) == (405, 0)


def test_sources_api_requires_login(f_anonymous_client: Client) -> None:
    assert f_anonymous_client.get('/api/v1/sources/').status_code == 403
