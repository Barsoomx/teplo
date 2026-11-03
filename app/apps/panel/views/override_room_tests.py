from collections.abc import Iterator
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock

import pytest
from django.test import Client
from freezegun import freeze_time

from apps.climate.domain.errors import RoomNotAdjustableError
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_intent import OverrideChange
from apps.climate.usecases.override_room import OverrideRoomInputDTO
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingProblem
from apps.house.domain.errors import RoomNotFoundError
from apps.panel.usecases.get_panel_overview import GetPanelOverviewOutputDTO

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 4, 16, 40, tzinfo=UTC)
URL = '/rooms/open-space/override/'


@pytest.fixture
def m_override_use_case() -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.override_room.OverrideRoomUseCase') as use_case:
        yield use_case


@pytest.fixture
def m_refusing_override_use_case(request: pytest.FixtureRequest) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.override_room.OverrideRoomUseCase') as use_case:
        use_case.init.return_value.execute.side_effect = request.param
        yield use_case


@pytest.fixture
def m_overview_use_case(panel_overview: GetPanelOverviewOutputDTO) -> Iterator[MagicMock]:
    with mock.patch('apps.panel.views.override_room.GetPanelOverviewUseCase') as use_case:
        use_case.init.return_value.execute.return_value = panel_overview
        yield use_case


@freeze_time(NOW)
@pytest.mark.parametrize(('step', 'change'), [('up', OverrideChange.UP), ('down', OverrideChange.DOWN)])
def test_step_goes_to_the_use_case_from_the_panel_and_the_dashboard_refreshes(
    f_client: Client,
    m_override_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    step: str,
    change: OverrideChange,
) -> None:
    response = f_client.post(URL, {'step': step})

    content = response.content.decode()
    assert (
        response.status_code,
        m_override_use_case.init.return_value.execute.call_args.args,
        'Кухня' in content,
        '<html' in content,
        m_overview_use_case.init.call_count,
    ) == (
        200,
        (
            OverrideRoomInputDTO(
                room='open-space',
                change=change,
                value=None,
                now=NOW,
                clock_trusted=True,
                source=CommandSource.PANEL,
            ),
        ),
        True,
        False,
        1,
    )


@pytest.mark.parametrize('posted', [{'step': 'warmer'}, {'step': 'set'}, {}])
def test_step_other_than_up_or_down_is_refused(
    f_client: Client,
    m_override_use_case: MagicMock,
    m_overview_use_case: MagicMock,
    posted: dict[str, str],
) -> None:
    response = f_client.post(URL, posted)

    assert (response.status_code, m_override_use_case.init.call_count, m_overview_use_case.init.call_count) == (
        400,
        0,
        0,
    )


@pytest.mark.parametrize(
    'm_refusing_override_use_case',
    [RoomNotFoundError('open-space'), RoomNotAdjustableError('open-space')],
    ids=['room_without_a_room_climate', 'room_without_an_air_reading'],
    indirect=True,
)
def test_step_for_a_room_that_takes_no_override_returns_404(
    f_client: Client,
    m_refusing_override_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'step': 'up'})

    assert (
        response.status_code,
        m_refusing_override_use_case.init.call_count,
        m_overview_use_case.init.call_count,
    ) == (
        404,
        1,
        0,
    )


@pytest.mark.parametrize(
    'm_refusing_override_use_case',
    [SettingsRejectedError((SettingProblem(field='target', code='out_of_range'),))],
    ids=['beyond_the_targets_range'],
    indirect=True,
)
def test_step_beyond_the_range_renders_the_dashboard_unchanged(
    f_client: Client,
    m_refusing_override_use_case: MagicMock,
    m_overview_use_case: MagicMock,
) -> None:
    response = f_client.post(URL, {'step': 'up'})

    assert (
        response.status_code,
        'Кухня' in response.content.decode(),
        m_refusing_override_use_case.init.call_count,
        m_overview_use_case.init.call_count,
    ) == (200, True, 1, 1)


def test_stepping_a_room_requires_login(f_anonymous_client: Client, m_override_use_case: MagicMock) -> None:
    response = f_anonymous_client.post(URL, {'step': 'up'})

    assert (response.status_code, response['Location'], m_override_use_case.init.call_count) == (
        302,
        f'/login/?next={URL}',
        0,
    )
