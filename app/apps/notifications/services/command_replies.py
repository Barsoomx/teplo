from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from django.template.defaultfilters import floatformat
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy

from apps.climate.domain.drive import PendingKind
from apps.climate.domain.room_intent import IntentReason, RoomIntent
from apps.climate.domain.room_status import RoomStatus, ZoneStatus
from apps.notifications.domain.alerts import PhaseReading

HELP = gettext_lazy(
    'Commands:\n'
    'гости — guest rooms at comfort until 14:00 (гости 2 — for two nights, гости всё — off)\n'
    'холодно в <room>, жарко в <room> — 1° warmer or cooler for 2 hours\n'
    '<room> 22 — hold 22° for 2 hours; <room> авто — back to the schedule\n'
    'буст <room> — warm floor for 2 hours\n'
    'статус — rooms, targets and phases',
)
UNKNOWN_ROOM = gettext_lazy('I do not know this room. Rooms: %(rooms)s')
AMBIGUOUS = gettext_lazy('Which room: %(rooms)s?')
GUESTS_SET = gettext_lazy('Guests until %(until)s: %(rooms)s at comfort.')
GUESTS_OFF = gettext_lazy('Guests mode is off.')
NO_GUEST_ROOMS = gettext_lazy('No room is a guest room: tick one on the settings page.')
NIGHTS = gettext_lazy('Guests: from 1 to 3 nights.')
GUESTS_CLOCK = gettext_lazy('The clock is not synced: guests can be switched on once it is.')
OVERRIDE = gettext_lazy('%(room)s: %(target)s° until %(until)s.')
AUTO = gettext_lazy('%(room)s: back to the schedule, %(target)s° until %(until)s.')
AUTO_OPEN = gettext_lazy('%(room)s: back to the schedule, %(target)s°.')
AUTO_NONE = gettext_lazy('%(room)s: no manual target to cancel.')
AUTO_DONE = gettext_lazy('%(room)s: back to the schedule.')
BOOST = gettext_lazy('%(room)s: boost until %(until)s.')
BOOST_FOR_COLD = gettext_lazy('%(room)s has no air sensor: boost until %(until)s.')
NO_FLOOR = gettext_lazy('%(room)s: no warm floor here.')
NO_AIR = gettext_lazy('%(room)s has no air sensor: only буст works there.')
NOTHING = gettext_lazy('%(room)s: teplo has nothing to heat here yet.')
RANGE = gettext_lazy('A target from %(low)s° to %(high)s°.')
BUSY = gettext_lazy('The database is busy: try again in a minute.')
NO_ROOMS = gettext_lazy('No rooms yet.')
BY_AIR = gettext_lazy('%(zone)s: by air')
FLOOR_HOLDS = gettext_lazy('%(zone)s: floor already at %(setpoint)s°')
FLOOR_AT = gettext_lazy('%(zone)s: floor %(setpoint)s° no sooner than %(time)s')
FLOOR_SOON = gettext_lazy('%(zone)s: floor %(setpoint)s° within the next minute')
FLOOR_WATCHED = gettext_lazy('%(zone)s: watching only, no command goes out')
FLOOR_SHED = gettext_lazy('%(zone)s: the floor is off by load management')
NO_CONNECTION = gettext_lazy('%(zone)s: no connection to the thermostat')
HEATING_OFF = gettext_lazy('%(zone)s: heating is off')
ROOM_AIR = gettext_lazy('%(room)s: %(air)s°, target %(target)s°, %(reason)s')
ROOM_TARGET = gettext_lazy('%(room)s: target %(target)s°, %(reason)s')
REASONS = {
    IntentReason.COMFORT: gettext_lazy('comfort until %(time)s'),
    IntentReason.SETBACK: gettext_lazy('night until %(time)s'),
    IntentReason.OVERRIDE: gettext_lazy('manual until %(time)s'),
    IntentReason.GUESTS: gettext_lazy('guests until %(time)s'),
}
OPEN_REASONS = {
    IntentReason.COMFORT: gettext_lazy('comfort'),
    IntentReason.SETBACK: gettext_lazy('night'),
    IntentReason.OVERRIDE: gettext_lazy('manual'),
    IntentReason.GUESTS: gettext_lazy('guests'),
    IntentReason.CLOCK: gettext_lazy('the clock is not synced'),
}
ZONE_AIR_FLOOR = gettext_lazy('%(zone)s: air %(air)s°, floor %(floor)s° (set to %(setpoint)s°)')
ZONE_FLOOR = gettext_lazy('%(zone)s: floor %(floor)s° (set to %(setpoint)s°)')
ZONE_OFF = gettext_lazy('%(zone)s: floor %(floor)s°, the thermostat is off')
PHASES = gettext_lazy('Phases: %(phases)s')
PHASE_AMPS = gettext_lazy('%(phase)s %(amps)s A')
ESTIMATED_PHASE_AMPS = gettext_lazy('%(phase)s ≈%(amps)s A')
TOMORROW = gettext_lazy('tomorrow')
OR = gettext_lazy('or')
PENDING_LINES = {
    PendingKind.HOLDS: FLOOR_HOLDS,
    PendingKind.WATCHING: FLOOR_WATCHED,
}
NAMES_SEPARATOR = '; '
PHASES_SEPARATOR = ' · '
MISSING = '—'


def moment_text(at: datetime, now: datetime) -> str:
    local = timezone.localtime(at)
    time = date_format(local, 'H:i')
    day = local.date()
    today = timezone.localtime(now).date()
    if day == today:
        return time

    other_day = str(TOMORROW) if day == today + timedelta(days=1) else date_format(day, 'j E')
    return f'{time} {other_day}'


def degrees(value: Decimal) -> str:
    return floatformat(value, 1)


def setpoint_degrees(value: Decimal) -> str:
    return floatformat(value, -1)


def room_names(names: Iterable[str]) -> str:
    return NAMES_SEPARATOR.join(names)


def either_of(names: Iterable[str]) -> str:
    return f' {OR!s} '.join(names)


def reason_text(intent: RoomIntent, now: datetime) -> str:
    if intent.until is None or intent.reason is IntentReason.CLOCK:
        return str(OPEN_REASONS[intent.reason])

    return str(REASONS[intent.reason]) % {'time': moment_text(intent.until, now)}


def room_line(room: RoomStatus, now: datetime) -> str:
    values = {'room': room.name, 'target': degrees(room.intent.target), 'reason': reason_text(room.intent, now)}
    if room.air is None:
        return str(ROOM_TARGET) % values

    return str(ROOM_AIR) % {**values, 'air': degrees(room.air)}


def zone_state_line(zone: ZoneStatus) -> str:
    if not zone.available or zone.floor_temperature is None:
        return str(NO_CONNECTION) % {'zone': zone.name}

    values = {'zone': zone.name, 'floor': degrees(zone.floor_temperature)}
    if not zone.power or zone.setpoint is None:
        return str(ZONE_OFF) % values

    values['setpoint'] = setpoint_degrees(zone.setpoint)
    if zone.air is None:
        return str(ZONE_FLOOR) % values

    return str(ZONE_AIR_FLOOR) % {**values, 'air': degrees(zone.air)}


def zone_write_line(zone: ZoneStatus, now: datetime) -> str:
    pending = zone.pending
    if zone.air_path or pending is None:
        return str(BY_AIR) % {'zone': zone.name}

    if pending.kind is PendingKind.HELD:
        return str(FLOOR_SHED) % {'zone': zone.name}

    if pending.kind is PendingKind.UNREACHED:
        return str(NO_CONNECTION) % {'zone': zone.name}

    if zone.floor_target is None:
        return str(HEATING_OFF) % {'zone': zone.name}

    values = {'zone': zone.name, 'setpoint': setpoint_degrees(zone.floor_target)}
    if pending.kind in PENDING_LINES:
        return str(PENDING_LINES[pending.kind]) % values

    if pending.at is None or pending.at <= now:
        return str(FLOOR_SOON) % values

    return str(FLOOR_AT) % {**values, 'time': moment_text(pending.at, now)}


def zone_write_lines(rooms: Iterable[RoomStatus], now: datetime) -> list[str]:
    return [zone_write_line(zone, now) for room in rooms for zone in room.zones]


def phases_line(phases: Sequence[PhaseReading]) -> str:
    return str(PHASES) % {'phases': PHASES_SEPARATOR.join(_phase_text(reading) for reading in phases)}


def _phase_text(reading: PhaseReading) -> str:
    phase = reading.phase.upper()
    if reading.amps is None:
        return f'{phase} {MISSING}'

    template = ESTIMATED_PHASE_AMPS if reading.estimated else PHASE_AMPS
    return str(template) % {'phase': phase, 'amps': floatformat(reading.amps, 1)}
