from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.optimal_start import NO_LEAD
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.domain.schedule import next_comfort_start, next_transition, resolve_mode
from apps.climate.domain.zone import AirTargets, ZoneSettings
from apps.climate.domain.zone_config import tenths
from apps.climate.domain.zone_intent import ZoneIntent
from apps.core.domain.settings import SettingProblem

OVERRIDE_DURATION = timedelta(hours=2)
OVERRIDE_STEP = Decimal('0.5')
COMMAND_STEP = Decimal('1.0')
OVERRIDE_TARGET_MIN = Decimal('10.0')
OVERRIDE_TARGET_MAX = Decimal('30.0')
MAX_GUEST_NIGHTS = 3
GUESTS_HORIZON = timedelta(days=MAX_GUEST_NIGHTS + 1)
GUESTS_NIGHT_ENDS_HOUR = 6
DAY = timedelta(days=1)


class IntentReason(StrEnum):
    OVERRIDE = 'override'
    CLOCK = 'clock'
    GUESTS = 'guests'
    COMFORT = 'comfort'
    SETBACK = 'setback'


class OverrideChange(StrEnum):
    UP = 'up'
    DOWN = 'down'
    WARMER = 'warmer'
    COOLER = 'cooler'
    SET = 'set'


@dataclass(frozen=True, kw_only=True)
class RoomIntent:
    room: str
    reason: IntentReason
    target: Decimal
    until: datetime | None
    forced: bool


@dataclass(frozen=True, kw_only=True)
class GuestsRecord:
    until: datetime | None
    end_hour: int


@dataclass(frozen=True, kw_only=True)
class RoomIntents:
    rooms: tuple[RoomClimateSettings, ...]
    by_room: Mapping[str, RoomIntent]

    def of(self, room: str) -> RoomIntent | None:
        return self.by_room.get(room)

    def zone_intent(self, zone: ZoneSettings, *, backed_up: frozenset[str]) -> ZoneIntent:
        backup = zone.slug in backed_up
        intent = None if zone.room_slug is None else self.of(zone.room_slug)
        if intent is None or not intent.forced:
            return ZoneIntent(target=None, backup=backup, learns=True)

        return ZoneIntent(target=intent.target, backup=backup, learns=False)


def resolve_room_intent(
    room: RoomClimateSettings,
    *,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
) -> RoomIntent:
    override = room.override
    if override is not None and now < override.until <= now + OVERRIDE_DURATION:
        return RoomIntent(
            room=room.slug,
            reason=IntentReason.OVERRIDE,
            target=override.target,
            until=override.until,
            forced=True,
        )

    if not clock_trusted:
        return RoomIntent(
            room=room.slug,
            reason=IntentReason.CLOCK,
            target=room.targets.comfort,
            until=None,
            forced=False,
        )

    if room.guest_room and guests_active(guests_until, now):
        return RoomIntent(
            room=room.slug,
            reason=IntentReason.GUESTS,
            target=room.targets.comfort,
            until=guests_until,
            forced=True,
        )

    if resolve_mode(room.windows, now, NO_LEAD) is ScheduleMode.COMFORT:
        transition = next_transition(room.windows, now, NO_LEAD)
        return RoomIntent(
            room=room.slug,
            reason=IntentReason.COMFORT,
            target=room.targets.comfort,
            until=None if transition is None else transition.at,
            forced=False,
        )

    return RoomIntent(
        room=room.slug,
        reason=IntentReason.SETBACK,
        target=room.targets.night,
        until=next_comfort_start(room.windows, now),
        forced=False,
    )


def resolve_room_intents(
    rooms: Iterable[RoomClimateSettings],
    *,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
) -> RoomIntents:
    settled = tuple(rooms)
    return RoomIntents(
        rooms=settled,
        by_room={
            room.slug: resolve_room_intent(room, now=now, clock_trusted=clock_trusted, guests_until=guests_until)
            for room in settled
        },
    )


def guests_active(until: datetime | None, now: datetime) -> bool:
    return until is not None and now < until <= now + GUESTS_HORIZON


def override_target(
    intent: RoomIntent,
    targets: AirTargets,
    change: OverrideChange,
    value: Decimal | None,
) -> Decimal:
    base = intent.target
    targets_by_change = {
        OverrideChange.UP: base + OVERRIDE_STEP,
        OverrideChange.DOWN: base - OVERRIDE_STEP,
        OverrideChange.WARMER: max(base + COMMAND_STEP, targets.comfort),
        OverrideChange.COOLER: base - COMMAND_STEP,
        OverrideChange.SET: base if value is None else value,
    }
    return tenths(targets_by_change[change])


def override_problems(target: Decimal) -> tuple[SettingProblem, ...]:
    if OVERRIDE_TARGET_MIN <= target <= OVERRIDE_TARGET_MAX:
        return ()

    return (SettingProblem(field='target', code='out_of_range'),)


def guests_end(now: datetime, nights: int, end_hour: int) -> datetime:
    first_day = now.date() if now.hour < GUESTS_NIGHT_ENDS_HOUR else now.date() + DAY
    end = datetime.combine(first_day + (nights - 1) * DAY, time(end_hour), tzinfo=now.tzinfo)
    return end if end > now else end + DAY
