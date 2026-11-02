import pytest

from apps.power.domain.outage import OutageCause, outage_answer


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('overload', OutageCause.OVERLOAD),
        ('grid', OutageCause.GRID),
        ('unknown', OutageCause.UNKNOWN),
        ('unmarked', None),
        ('meteor', None),
        ('', None),
    ],
)
def test_answer_is_one_of_the_three_causes_the_owner_can_give(value: str, expected: OutageCause | None) -> None:
    assert outage_answer(value) is expected
