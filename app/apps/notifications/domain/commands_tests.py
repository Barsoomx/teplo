from decimal import Decimal

import pytest

from apps.notifications.domain.commands import CommandKind, CommandRoom, OwnerCommand, parse_command

BATHROOM = ('санузел', 'санузл', 'ванн', 'туалет')
OPEN_SPACE = CommandRoom(
    slug='open-space',
    name='Гостиная, кухня и прихожая',
    level=1,
    stems=('гостин', 'кухн', 'прихож', 'зал'),
)
BATHROOM_1F = CommandRoom(slug='bathroom-1f', name='Санузел 1 этаж', level=1, stems=BATHROOM)
BEDROOM = CommandRoom(slug='bedroom', name='Спальня', level=2, stems=('спальн',))
OFFICE = CommandRoom(slug='office', name='Кабинет', level=2, stems=('кабинет', 'офис'))
BATHROOM_2F = CommandRoom(slug='bathroom-2f', name='Санузел 2 этаж', level=2, stems=BATHROOM)
ROOMS = (OPEN_SPACE, BATHROOM_1F, BEDROOM, OFFICE, BATHROOM_2F)
ATTIC = CommandRoom(slug='attic', name='Чердак', level=3, stems=())
UNNAMED_BATHROOMS = (
    CommandRoom(slug='bathroom-1f', name='Санузел 1 этаж', level=1, stems=()),
    CommandRoom(slug='bathroom-2f', name='Санузел 2 этаж', level=2, stems=()),
)


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('статус', OwnerCommand(kind=CommandKind.STATUS)),
        ('Гости', OwnerCommand(kind=CommandKind.GUESTS, nights=1)),
        ('гости 2', OwnerCommand(kind=CommandKind.GUESTS, nights=2)),
        ('гости всё', OwnerCommand(kind=CommandKind.GUESTS_OFF)),
        ('Гости уехали', OwnerCommand(kind=CommandKind.GUESTS_OFF)),
        ('гости завтра', OwnerCommand(kind=CommandKind.HELP)),
        ('Гости 3 ночи', OwnerCommand(kind=CommandKind.GUESTS, nights=3)),
        ('гости 1 ночь', OwnerCommand(kind=CommandKind.GUESTS, nights=1)),
        ('Гости все здесь', OwnerCommand(kind=CommandKind.HELP)),
        ('гости 2 человека', OwnerCommand(kind=CommandKind.HELP)),
        ('гости уехали вчера', OwnerCommand(kind=CommandKind.HELP)),
        ('холодно в гостиной', OwnerCommand(kind=CommandKind.WARMER, room=OPEN_SPACE)),
        ('Жарко на кухне!', OwnerCommand(kind=CommandKind.COOLER, room=OPEN_SPACE)),
        ('гостиная 22,5', OwnerCommand(kind=CommandKind.SET, room=OPEN_SPACE, value=Decimal('22.5'))),
        ('Гостиная 22.', OwnerCommand(kind=CommandKind.SET, room=OPEN_SPACE, value=Decimal('22'))),
        ('кабинет авто', OwnerCommand(kind=CommandKind.AUTO, room=OFFICE)),
        ('буст санузел 2', OwnerCommand(kind=CommandKind.BOOST, room=BATHROOM_2F)),
        ('холодно в санузле', OwnerCommand(kind=CommandKind.AMBIGUOUS, candidates=(BATHROOM_1F, BATHROOM_2F))),
        ('холодно в санузле на втором этаже', OwnerCommand(kind=CommandKind.WARMER, room=BATHROOM_2F)),
        ('холодно в ванной 1 этажа', OwnerCommand(kind=CommandKind.WARMER, room=BATHROOM_1F)),
        ('кабинет на первом этаже авто', OwnerCommand(kind=CommandKind.AUTO, room=OFFICE)),
        ('холодно на чердаке', OwnerCommand(kind=CommandKind.UNKNOWN_ROOM, candidates=ROOMS)),
        ('холодно', OwnerCommand(kind=CommandKind.UNKNOWN_ROOM, candidates=ROOMS)),
        ('гостиная 225', OwnerCommand(kind=CommandKind.HELP)),
        ('Топлю камин', OwnerCommand(kind=CommandKind.FIREPLACE_LIT)),
        ('камин топлю', OwnerCommand(kind=CommandKind.FIREPLACE_LIT)),
        ('разжёг камин!', OwnerCommand(kind=CommandKind.FIREPLACE_LIT)),
        ('Камин погас', OwnerCommand(kind=CommandKind.FIREPLACE_OUT)),
        ('камин потух', OwnerCommand(kind=CommandKind.FIREPLACE_OUT)),
        ('прогорел камин', OwnerCommand(kind=CommandKind.FIREPLACE_OUT)),
        ('топлю камин сейчас', OwnerCommand(kind=CommandKind.HELP)),
        ('камин', OwnerCommand(kind=CommandKind.HELP)),
        ('камин горит', OwnerCommand(kind=CommandKind.HELP)),
        ('привет', OwnerCommand(kind=CommandKind.HELP)),
        ('', OwnerCommand(kind=CommandKind.HELP)),
        ('?!', OwnerCommand(kind=CommandKind.HELP)),
    ],
    ids=[
        'status',
        'guests',
        'guests_for_two_nights',
        'guests_off',
        'guests_left',
        'guests_and_an_unknown_word',
        'guests_for_nights_in_words',
        'guests_for_one_night_in_words',
        'a_sentence_that_starts_with_a_stop_word',
        'a_sentence_that_starts_with_a_number',
        'guests_left_and_more',
        'warmer',
        'cooler_on_a_stem',
        'set_with_a_decimal_comma',
        'set_with_a_trailing_point',
        'auto',
        'boost_on_the_second_floor',
        'ambiguous',
        'floor_in_words',
        'floor_in_digits',
        'floor_that_names_no_candidate',
        'unknown_room',
        'no_room',
        'target_out_of_shape',
        'fireplace_lit',
        'fireplace_lit_in_either_order',
        'fireplace_lit_in_another_word',
        'fireplace_out',
        'fireplace_out_in_another_word',
        'fireplace_out_in_either_order',
        'three_words_about_the_fireplace',
        'fireplace_alone',
        'fireplace_and_an_unknown_word',
        'unknown_command',
        'empty',
        'punctuation_only',
    ],
)
def test_parse_command_reads_the_owner_s_words(text: str, expected: OwnerCommand) -> None:
    assert parse_command(text, ROOMS) == expected


@pytest.mark.parametrize(
    ('text', 'rooms', 'expected'),
    [
        ('чердак 21', (*ROOMS, ATTIC), OwnerCommand(kind=CommandKind.SET, room=ATTIC, value=Decimal('21'))),
        ('санузел 2 этаж авто', UNNAMED_BATHROOMS, OwnerCommand(kind=CommandKind.AUTO, room=UNNAMED_BATHROOMS[1])),
    ],
    ids=['by_its_name', 'by_its_name_and_floor'],
)
def test_a_room_without_stems_is_found_by_its_name(
    text: str,
    rooms: tuple[CommandRoom, ...],
    expected: OwnerCommand,
) -> None:
    assert parse_command(text, rooms) == expected
