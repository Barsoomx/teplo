import pytest

from settings.utils import to_bool, to_list


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('true', True),
        ('YES', True),
        (' 1 ', True),
        ('on', True),
        ('false', False),
        ('0', False),
        ('', False),
    ],
)
def test_to_bool_parses_known_values(raw: str, expected: bool) -> None:
    assert to_bool(raw) is expected


def test_to_bool_returns_default_for_missing_value() -> None:
    assert to_bool(None, default=True) is True


def test_to_list_splits_and_strips() -> None:
    assert to_list('a, b,,c ') == ['a', 'b', 'c']


def test_to_list_returns_empty_for_missing_value() -> None:
    assert to_list(None) == []
