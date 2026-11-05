from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from django.template.defaultfilters import floatformat
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy

from apps.climate.domain.drive import PendingKind
from apps.climate.domain.fast_source import OWNER_MODES
from apps.climate.domain.room_intent import OCCUPANCY_REASONS, IntentReason, RoomIntent
from apps.climate.domain.room_status import RoomStatus, SourceStatus, ZoneStatus
from apps.devices.domain.readings import ClimateMode
from apps.notifications.domain.alerts import PhaseReading
from apps.presence.domain.occupancy import Occupancy

HELP = gettext_lazy(
    'Commands:\n'
    'гости — guest rooms at comfort until 14:00 (гости 2 — for two nights, гости всё — off)\n'
    'холодно в <room>, жарко в <room> — 1° warmer or cooler for 2 hours\n'
    '<room> 22 — hold 22° for 2 hours; <room> авто — back to the schedule\n'
    'буст <room> — warm floor for 2 hours\n'
    'топлю камин — mark the fireplace lit for 4 hours; камин погас — clear the mark\n'
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
AUTO_OCCUPANCY = gettext_lazy('%(room)s: back to occupancy, %(target)s°.')
AUTO_NONE = gettext_lazy('%(room)s: no manual target to cancel.')
AUTO_DONE = gettext_lazy('%(room)s: back to the schedule.')
AUTO_DONE_OCCUPANCY = gettext_lazy('%(room)s: back to occupancy.')
BOOST = gettext_lazy('%(room)s: boost until %(until)s.')
BOOST_FOR_COLD = gettext_lazy('%(room)s has no air sensor: boost until %(until)s.')
NO_FLOOR = gettext_lazy('%(room)s: no warm floor here.')
NO_AIR = gettext_lazy('%(room)s has no air sensor: only буст works there.')
NOTHING = gettext_lazy('%(room)s: teplo has nothing to heat here yet.')
RANGE = gettext_lazy('A target from %(low)s° to %(high)s°.')
BUSY = gettext_lazy('The database is busy: try again in a minute.')
FIREPLACE_LIT = gettext_lazy('The fireplace is lit until %(until)s.')
FIREPLACE_OUT = gettext_lazy('The fireplace is out.')
FIREPLACE_NOT_MARKED = gettext_lazy('The fireplace is not marked anyway.')
NO_FIREPLACE = gettext_lazy('The house has no fireplace.')
NO_ROOMS = gettext_lazy('No rooms yet.')
BY_AIR = gettext_lazy('%(zone)s: by air')
FLOOR_HOLDS = gettext_lazy('%(zone)s: floor already at %(setpoint)s°')
FLOOR_AT = gettext_lazy('%(zone)s: floor %(setpoint)s° no sooner than %(time)s')
FLOOR_SOON = gettext_lazy('%(zone)s: floor %(setpoint)s° within the next minute')
FLOOR_WATCHED = gettext_lazy('%(zone)s: watching only, no command goes out')
FLOOR_SHED = gettext_lazy('%(zone)s: the floor is off by load management')
FLOOR_HANDS_OFF = gettext_lazy('%(zone)s: changed on the thermostat, teplo leaves it alone until %(time)s')
NO_CONNECTION = gettext_lazy('%(zone)s: no connection to the thermostat')
SOURCE_SOON = gettext_lazy('%(source)s: %(target)s within the next minute')
SOURCE_AT = gettext_lazy('%(source)s: %(target)s no sooner than %(time)s')
SOURCE_WATCHED = gettext_lazy('%(source)s: %(target)s — watching only, no command goes out')
SOURCE_SHED = gettext_lazy('%(source)s: off by load management')
SOURCE_HANDS_OFF = gettext_lazy('%(source)s: changed on the unit, teplo leaves it alone until %(time)s')
SOURCE_HOLDS = gettext_lazy('%(source)s: already %(target)s')
SOURCE_HOLDS_OFF = gettext_lazy('%(source)s: already off')
SOURCE_NO_CONNECTION = gettext_lazy('%(source)s: no connection')
SOURCE_SWITCH_OFF = gettext_lazy('switch-off')
SOURCE_ON = gettext_lazy('%(source)s: on at %(setpoint)s°, %(temperature)s° at the unit')
SOURCE_ON_UNREAD = gettext_lazy('%(source)s: on at %(setpoint)s°')
SOURCE_OFF = gettext_lazy('%(source)s: off, %(temperature)s° at the unit')
SOURCE_OFF_UNREAD = gettext_lazy('%(source)s: off')
SOURCE_OWNER_MODE = gettext_lazy('%(source)s: %(mode)s, %(setpoint)s°, teplo leaves it alone')
SOURCE_MODE = gettext_lazy('%(source)s: %(mode)s, %(setpoint)s°')
SOURCE_FROST_LOCKOUT = gettext_lazy('%(line)s; frost lockout, %(temperature)s° outside')
SOURCE_LOCKED_WITHOUT_OUTDOOR = gettext_lazy('%(line)s; locked out: no outdoor reading')
MODE_WORDS = {
    ClimateMode.HEAT: gettext_lazy('heating'),
    ClimateMode.COOL: gettext_lazy('cooling'),
    ClimateMode.AUTO: gettext_lazy('auto'),
    ClimateMode.OTHER: gettext_lazy('another mode'),
    ClimateMode.OFF: gettext_lazy('off'),
}
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
    IntentReason.OCCUPIED: gettext_lazy('occupied'),
    IntentReason.VACANT: gettext_lazy('nobody there'),
}
OCCUPANCY_WORDS = {
    Occupancy.OCCUPIED: gettext_lazy('occupied'),
    Occupancy.VACANT: gettext_lazy('nobody there'),
    Occupancy.UNKNOWN: gettext_lazy('no presence data'),
}
OCCUPANCY_WATCHED = gettext_lazy('%(occupancy)s (watching)')
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
        return str(ROOM_TARGET) % values + _occupancy_text(room)

    return str(ROOM_AIR) % {**values, 'air': degrees(room.air)} + _occupancy_text(room)


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
    if pending is not None and pending.kind is PendingKind.HANDS_OFF and pending.at is not None:
        return str(FLOOR_HANDS_OFF) % {'zone': zone.name, 'time': moment_text(pending.at, now)}

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


def source_write_line(source: SourceStatus, now: datetime) -> str | None:
    pending = source.pending
    if pending is None:
        return None

    values = {'source': source.name, 'target': _source_target(source)}
    if pending.kind is PendingKind.HANDS_OFF and pending.at is not None:
        return str(SOURCE_HANDS_OFF) % {**values, 'time': moment_text(pending.at, now)}

    lines = {
        PendingKind.HELD: SOURCE_SHED,
        PendingKind.UNREACHED: SOURCE_NO_CONNECTION,
        PendingKind.HOLDS: SOURCE_HOLDS if source.target_on else SOURCE_HOLDS_OFF,
        PendingKind.WATCHING: SOURCE_WATCHED,
    }
    if pending.kind in lines:
        return str(lines[pending.kind]) % values

    if pending.at is None or pending.at <= now:
        return str(SOURCE_SOON) % values

    return str(SOURCE_AT) % {**values, 'time': moment_text(pending.at, now)}


def source_write_lines(rooms: Iterable[RoomStatus], now: datetime) -> list[str]:
    return [line for room in rooms for source in room.sources if (line := source_write_line(source, now)) is not None]


def source_state_line(source: SourceStatus) -> str:
    line = _source_state(source)
    if not source.locked_out:
        return line

    if source.outdoor is None:
        return str(SOURCE_LOCKED_WITHOUT_OUTDOOR) % {'line': line}

    return str(SOURCE_FROST_LOCKOUT) % {'line': line, 'temperature': setpoint_degrees(source.outdoor)}


def phases_line(phases: Sequence[PhaseReading]) -> str:
    return str(PHASES) % {'phases': PHASES_SEPARATOR.join(_phase_text(reading) for reading in phases)}


def _phase_text(reading: PhaseReading) -> str:
    phase = reading.phase.upper()
    if reading.amps is None:
        return f'{phase} {MISSING}'

    template = ESTIMATED_PHASE_AMPS if reading.estimated else PHASE_AMPS
    return str(template) % {'phase': phase, 'amps': floatformat(reading.amps, 1)}


def _occupancy_text(room: RoomStatus) -> str:
    if room.occupancy is None or room.intent.reason in OCCUPANCY_REASONS:
        return ''

    word = str(OCCUPANCY_WORDS[room.occupancy])
    if not room.follows_occupancy:
        word = str(OCCUPANCY_WATCHED) % {'occupancy': word}
    return f'{NAMES_SEPARATOR}{word}'


def _source_state(source: SourceStatus) -> str:
    if not source.available or source.on is None:
        return str(SOURCE_NO_CONNECTION) % {'source': source.name}

    if not source.on or source.setpoint is None:
        if source.temperature is None:
            return str(SOURCE_OFF_UNREAD) % {'source': source.name}

        return str(SOURCE_OFF) % {'source': source.name, 'temperature': setpoint_degrees(source.temperature)}

    values = {'source': source.name, 'setpoint': setpoint_degrees(source.setpoint)}
    if source.mode is not None and source.mode is not ClimateMode.HEAT:
        template = SOURCE_OWNER_MODE if source.mode in OWNER_MODES else SOURCE_MODE
        return str(template) % {**values, 'mode': MODE_WORDS[source.mode]}

    if source.temperature is None:
        return str(SOURCE_ON_UNREAD) % values

    return str(SOURCE_ON) % {**values, 'temperature': setpoint_degrees(source.temperature)}


def _source_target(source: SourceStatus) -> str:
    if not source.target_on or source.target_setpoint is None:
        return str(SOURCE_SWITCH_OFF)

    return f'{setpoint_degrees(source.target_setpoint)}°'
