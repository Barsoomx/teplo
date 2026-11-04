import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

DECIMAL_COMMA = re.compile(r'(?<=\d),(?=\d)')
SEPARATORS = re.compile(r'[^\w.]+')
TARGET = re.compile(r'[0-9]{1,2}(\.[0-9])?')
WHOLE_NUMBER = re.compile(r'[0-9]+')
FLOOR_NUMBER = re.compile(r'[1-9]')
PREPOSITIONS = frozenset({'в', 'во', 'на', 'у'})
GUESTS_LEFT = frozenset({'все', 'стоп', 'нет', 'уехали', 'выкл'})
NIGHTS_WORD = 'ноч'
FLOOR_ORDINALS = (('перв', 1), ('втор', 2), ('трет', 3))
FLOOR_WORD = 'этаж'
STATUS_WORD = 'статус'
GUESTS_WORD = 'гост'
LIVING_ROOM_WORD = 'гостин'
AUTO_WORD = 'авто'
FIREPLACE_WORD = 'камин'


class CommandKind(StrEnum):
    STATUS = 'status'
    GUESTS = 'guests'
    GUESTS_OFF = 'guests_off'
    WARMER = 'warmer'
    COOLER = 'cooler'
    SET = 'set'
    AUTO = 'auto'
    BOOST = 'boost'
    FIREPLACE_LIT = 'fireplace_lit'
    FIREPLACE_OUT = 'fireplace_out'
    HELP = 'help'
    UNKNOWN_ROOM = 'unknown_room'
    AMBIGUOUS = 'ambiguous'


ROOM_WORDS = (('холодн', CommandKind.WARMER), ('жарк', CommandKind.COOLER), ('буст', CommandKind.BOOST))
FIREPLACE_WORDS = (
    ('топл', CommandKind.FIREPLACE_LIT),
    ('разж', CommandKind.FIREPLACE_LIT),
    ('погас', CommandKind.FIREPLACE_OUT),
    ('потух', CommandKind.FIREPLACE_OUT),
    ('прогор', CommandKind.FIREPLACE_OUT),
)


@dataclass(frozen=True, kw_only=True)
class CommandRoom:
    slug: str
    name: str
    level: int
    stems: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class OwnerCommand:
    kind: CommandKind
    room: CommandRoom | None = None
    value: Decimal | None = None
    nights: int | None = None
    candidates: tuple[CommandRoom, ...] = ()


@dataclass(frozen=True, kw_only=True)
class RoomMention:
    words: tuple[str, ...]
    levels: frozenset[int]


def parse_command(text: str, rooms: Sequence[CommandRoom]) -> OwnerCommand:
    words = words_of(text)
    if not words:
        return OwnerCommand(kind=CommandKind.HELP)

    first = words[0]
    if first.startswith(STATUS_WORD):
        return OwnerCommand(kind=CommandKind.STATUS)

    fireplace = _fireplace(words)
    if fireplace is not None:
        return OwnerCommand(kind=fireplace)

    if first.startswith(GUESTS_WORD) and not first.startswith(LIVING_ROOM_WORD):
        return _guests(words[1:])

    for prefix, kind in ROOM_WORDS:
        if first.startswith(prefix):
            return _for_room(OwnerCommand(kind=kind), words[1:], rooms)

    return _setting(words, rooms)


def words_of(text: str) -> tuple[str, ...]:
    lowered = DECIMAL_COMMA.sub('.', text.lower().replace('ё', 'е'))
    return tuple(word for word in (part.strip('.') for part in SEPARATORS.split(lowered)) if word)


def room_mention(words: Sequence[str]) -> RoomMention:
    named: list[str] = []
    levels: set[int] = set()
    for word in words:
        level = _floor_level(word)
        if level is not None:
            levels.add(level)
        elif word not in PREPOSITIONS and not word.startswith(FLOOR_WORD):
            named.append(word)
    return RoomMention(words=tuple(named), levels=frozenset(levels))


def _fireplace(words: Sequence[str]) -> CommandKind | None:
    if len(words) != 2:
        return None

    first, second = words
    if first.startswith(FIREPLACE_WORD):
        return _fireplace_change(second)

    if second.startswith(FIREPLACE_WORD):
        return _fireplace_change(first)

    return None


def _fireplace_change(word: str) -> CommandKind | None:
    return next((kind for prefix, kind in FIREPLACE_WORDS if word.startswith(prefix)), None)


def _guests(words: Sequence[str]) -> OwnerCommand:
    if not words:
        return OwnerCommand(kind=CommandKind.GUESTS, nights=1)

    first, *rest = words
    if first in GUESTS_LEFT and not rest:
        return OwnerCommand(kind=CommandKind.GUESTS_OFF)

    if WHOLE_NUMBER.fullmatch(first) and len(rest) <= 1 and all(word.startswith(NIGHTS_WORD) for word in rest):
        return OwnerCommand(kind=CommandKind.GUESTS, nights=int(first))

    return OwnerCommand(kind=CommandKind.HELP)


def _setting(words: Sequence[str], rooms: Sequence[CommandRoom]) -> OwnerCommand:
    *named, last = words
    if last == AUTO_WORD:
        return _for_room(OwnerCommand(kind=CommandKind.AUTO), named, rooms)

    if TARGET.fullmatch(last):
        return _for_room(OwnerCommand(kind=CommandKind.SET, value=Decimal(last)), named, rooms)

    return OwnerCommand(kind=CommandKind.HELP)


def _for_room(command: OwnerCommand, words: Sequence[str], rooms: Sequence[CommandRoom]) -> OwnerCommand:
    candidates = _candidates(room_mention(words), rooms)
    if not candidates:
        return OwnerCommand(kind=CommandKind.UNKNOWN_ROOM, candidates=tuple(rooms))

    if len(candidates) > 1:
        return OwnerCommand(kind=CommandKind.AMBIGUOUS, candidates=candidates)

    return replace(command, room=candidates[0])


def _candidates(mention: RoomMention, rooms: Sequence[CommandRoom]) -> tuple[CommandRoom, ...]:
    if not mention.words:
        return ()

    named = _by_stem(mention.words, rooms) or _by_name(mention.words, rooms)
    return tuple(room for room in named if room.level in mention.levels) or named


def _by_stem(words: Sequence[str], rooms: Sequence[CommandRoom]) -> tuple[CommandRoom, ...]:
    return tuple(room for room in rooms if any(word.startswith(stem) for stem in room.stems for word in words))


def _by_name(words: Sequence[str], rooms: Sequence[CommandRoom]) -> tuple[CommandRoom, ...]:
    return tuple(room for room in rooms if room_mention(words_of(room.name)).words == tuple(words))


def _floor_level(word: str) -> int | None:
    if FLOOR_NUMBER.fullmatch(word):
        return int(word)

    return next((level for prefix, level in FLOOR_ORDINALS if word.startswith(prefix)), None)
