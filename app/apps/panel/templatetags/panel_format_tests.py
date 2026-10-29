from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from apps.panel.templatetags.panel_format import command_summary, day_word, icon, local_time

MOSCOW = ZoneInfo('Europe/Moscow')


def test_icon_references_the_sprite() -> None:
    assert icon('flame') == (
        '<svg class="icon" aria-hidden="true" focusable="false"><use href="/static/panel/icons.svg#flame"></use></svg>'
    )


@pytest.mark.parametrize(
    ('change', 'expected'),
    [
        ({'power': True, 'mode': 'hold', 'setpoint': '18.0'}, 'turn on, manual mode, set to 18°'),
        ({'power': None, 'mode': None, 'setpoint': '21.5'}, 'set to 21.5°'),
        ({'power': False, 'mode': None, 'setpoint': None}, 'turn off'),
    ],
)
def test_command_summary_lists_the_changed_settings(change: dict[str, object], expected: str) -> None:
    assert command_summary(change) == expected


def test_local_time_shows_journal_timestamps_in_house_time() -> None:
    assert local_time('2026-10-29T21:15:00Z') == '00:15'


@freeze_time('2026-10-29T17:00:00Z')
@pytest.mark.parametrize(
    ('moment', 'expected'),
    [
        (datetime(2026, 10, 29, 23, 30, tzinfo=MOSCOW), ''),
        (datetime(2026, 10, 30, 3, 30, tzinfo=MOSCOW), 'tomorrow'),
        (datetime(2026, 11, 6, 6, 30, tzinfo=MOSCOW), '6 November'),
    ],
)
def test_day_word_names_days_other_than_today(moment: datetime, expected: str) -> None:
    assert day_word(moment) == expected
