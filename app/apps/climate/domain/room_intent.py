from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

from apps.climate.domain.heat_sources import FireplaceRecord
from apps.climate.domain.modes import ScheduleMode
from apps.climate.domain.optimal_start import NO_LEAD
from apps.climate.domain.room_climate import RoomClimateSettings
from apps.climate.domain.schedule import next_comfort_start, next_transition, resolve_mode
from apps.climate.domain.zone import AirTargets, ZoneSettings
from apps.climate.domain.zone_config import tenths
from apps.climate.domain.zone_intent import ForcedTarget, ZoneIntent
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
    forced: tuple[ForcedTarget, ...]


@dataclass(frozen=True, kw_only=True)
class GuestsRecord:
    until: datetime | None
    end_hour: int


@dataclass(frozen=True, kw_only=True)
class RoomIntents:
    rooms: tuple[RoomClimateSettings, ...]
    by_room: Mapping[str, RoomIntent]
    now: datetime
    fireplaces: tuple[FireplaceRecord, ...] = ()

    def of(self, room: str) -> RoomIntent | None:
        return self.by_room.get(room)

    def fireplace(self, room: str) -> FireplaceRecord | None:
        return next((fireplace for fireplace in self.fireplaces if fireplace.room_slug == room), None)

    def lit_rooms(self) -> frozenset[str]:
        return frozenset(fireplace.room_slug for fireplace in self.fireplaces if fireplace.lit(self.now))

    def zone_intent(
        self,
        zone: ZoneSettings,
        *,
        backed_up: frozenset[str],
        heated: frozenset[str] = frozenset(),
    ) -> ZoneIntent:
        intent = None if zone.room_slug is None else self.of(zone.room_slug)
        forced = () if intent is None else intent.forced
        return ZoneIntent(
            forced=forced,
            backup=zone.slug in backed_up,
            learns=not forced and zone.room_slug not in heated,
        )


def resolve_room_intent(
    room: RoomClimateSettings,
    *,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
) -> RoomIntent:
    beneath = _beneath_override(room, now=now, clock_trusted=clock_trusted, guests_until=guests_until)
    override = room.override
    if override is None or not now < override.until <= now + OVERRIDE_DURATION:
        return beneath

    return RoomIntent(
        room=room.slug,
        reason=IntentReason.OVERRIDE,
        target=override.target,
        until=override.until,
        forced=(*beneath.forced, ForcedTarget(target=override.target, beneath=beneath.target)),
    )


def resolve_room_intents(
    rooms: Iterable[RoomClimateSettings],
    *,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
    fireplaces: Iterable[FireplaceRecord] = (),
) -> RoomIntents:
    settled = tuple(rooms)
    return RoomIntents(
        rooms=settled,
        by_room={
            room.slug: resolve_room_intent(room, now=now, clock_trusted=clock_trusted, guests_until=guests_until)
            for room in settled
        },
        now=now,
        fireplaces=tuple(fireplaces),
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
    first = datetime.combine(first_day, time(end_hour), tzinfo=now.tzinfo)
    checkout = first if first > now else first + DAY
    return checkout + (nights - 1) * DAY


def _beneath_override(
    room: RoomClimateSettings,
    *,
    now: datetime,
    clock_trusted: bool,
    guests_until: datetime | None,
) -> RoomIntent:
    if not clock_trusted:
        return RoomIntent(
            room=room.slug,
            reason=IntentReason.CLOCK,
            target=room.targets.comfort,
            until=None,
            forced=(),
        )

    scheduled = _scheduled(room, now)
    if not room.guest_room or not guests_active(guests_until, now):
        return scheduled

    return RoomIntent(
        room=room.slug,
        reason=IntentReason.GUESTS,
        target=room.targets.comfort,
        until=guests_until,
        forced=(ForcedTarget(target=room.targets.comfort, beneath=scheduled.target),),
    )


def _scheduled(room: RoomClimateSettings, now: datetime) -> RoomIntent:
    if resolve_mode(room.windows, now, NO_LEAD) is ScheduleMode.COMFORT:
        transition = next_transition(room.windows, now, NO_LEAD)
        return RoomIntent(
            room=room.slug,
            reason=IntentReason.COMFORT,
            target=room.targets.comfort,
            until=None if transition is None else transition.at,
            forced=(),
        )

    return RoomIntent(
        room=room.slug,
        reason=IntentReason.SETBACK,
        target=room.targets.night,
        until=next_comfort_start(room.windows, now),
        forced=(),
    )
