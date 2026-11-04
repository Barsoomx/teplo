from collections.abc import Collection, Sequence
from datetime import datetime

from django.conf import settings
from django.db import OperationalError
from django.utils import translation
from django.utils.functional import Promise

from apps.climate.domain.errors import (
    ClockUntrustedError,
    FireplaceNotFoundError,
    RoomNotAdjustableError,
    ZoneNotFoundError,
)
from apps.climate.domain.modes import CommandSource
from apps.climate.domain.room_intent import (
    MAX_GUEST_NIGHTS,
    OVERRIDE_TARGET_MAX,
    OVERRIDE_TARGET_MIN,
    IntentReason,
    OverrideChange,
)
from apps.climate.domain.room_status import RoomStatus
from apps.climate.services.room_status import RoomStatusReader
from apps.climate.usecases.boost_zones import BoostZonesInputDTO, BoostZonesOutputDTO, BoostZonesUseCase
from apps.climate.usecases.clear_room_override import (
    ClearRoomOverrideInputDTO,
    ClearRoomOverrideOutputDTO,
    ClearRoomOverrideUseCase,
)
from apps.climate.usecases.mark_fireplace import (
    MarkFireplaceInputDTO,
    MarkFireplaceOutputDTO,
    MarkFireplaceUseCase,
)
from apps.climate.usecases.override_room import OverrideRoomInputDTO, OverrideRoomOutputDTO, OverrideRoomUseCase
from apps.climate.usecases.set_guests import SetGuestsInputDTO, SetGuestsOutputDTO, SetGuestsUseCase
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.ports import Executable
from apps.house.domain.errors import RoomNotFoundError
from apps.notifications.domain.commands import CommandKind, CommandRoom, OwnerCommand
from apps.notifications.domain.ports import PhaseReadout, RoomStatusSource
from apps.notifications.services.command_replies import (
    AMBIGUOUS,
    AUTO,
    AUTO_DONE,
    AUTO_NONE,
    AUTO_OPEN,
    BOOST,
    BOOST_FOR_COLD,
    BUSY,
    FIREPLACE_LIT,
    FIREPLACE_NOT_MARKED,
    FIREPLACE_OUT,
    GUESTS_CLOCK,
    GUESTS_OFF,
    GUESTS_SET,
    HELP,
    NIGHTS,
    NO_AIR,
    NO_FIREPLACE,
    NO_FLOOR,
    NO_GUEST_ROOMS,
    NO_ROOMS,
    NOTHING,
    OVERRIDE,
    RANGE,
    UNKNOWN_ROOM,
    degrees,
    either_of,
    moment_text,
    phases_line,
    room_line,
    room_names,
    setpoint_degrees,
    zone_state_line,
    zone_write_lines,
)
from apps.notifications.services.phase_readout import PhaseMeterReadout

CHANGES = {
    CommandKind.WARMER: OverrideChange.WARMER,
    CommandKind.COOLER: OverrideChange.COOLER,
    CommandKind.SET: OverrideChange.SET,
}
FIREPLACE_MARKS = {CommandKind.FIREPLACE_LIT: True, CommandKind.FIREPLACE_OUT: False}


class ClimateCommandRunner:
    def __init__(
        self,
        guests: Executable[SetGuestsInputDTO, SetGuestsOutputDTO],
        override: Executable[OverrideRoomInputDTO, OverrideRoomOutputDTO],
        clear: Executable[ClearRoomOverrideInputDTO, ClearRoomOverrideOutputDTO],
        boost: Executable[BoostZonesInputDTO, BoostZonesOutputDTO],
        fireplace: Executable[MarkFireplaceInputDTO, MarkFireplaceOutputDTO],
        rooms: RoomStatusSource,
        phases: PhaseReadout,
        language: str,
    ) -> None:
        self._set_guests = guests
        self._override_room = override
        self._clear_override = clear
        self._boost_zones = boost
        self._mark_fireplace = fireplace
        self._rooms = rooms
        self._phases = phases
        self._language = language

    @classmethod
    def init(cls) -> 'ClimateCommandRunner':
        return cls(
            guests=SetGuestsUseCase.init(),
            override=OverrideRoomUseCase.init(),
            clear=ClearRoomOverrideUseCase.init(),
            boost=BoostZonesUseCase.init(),
            fireplace=MarkFireplaceUseCase.init(),
            rooms=RoomStatusReader.init(),
            phases=PhaseMeterReadout.init(),
            language=settings.TEPLO_ALERT_LANGUAGE,
        )

    def run(self, command: OwnerCommand, now: datetime, *, clock_trusted: bool) -> str:
        with translation.override(self._language):
            try:
                return self._reply(command, now, clock_trusted=clock_trusted)
            except OperationalError:
                return str(BUSY)

    def _reply(self, command: OwnerCommand, now: datetime, *, clock_trusted: bool) -> str:
        if command.kind is CommandKind.STATUS:
            return self._status(now, clock_trusted=clock_trusted)

        if command.kind is CommandKind.GUESTS:
            return self._guests(command.nights or 0, now, clock_trusted=clock_trusted)

        if command.kind in FIREPLACE_MARKS:
            return self._fireplace(FIREPLACE_MARKS[command.kind], now)

        if command.kind is CommandKind.GUESTS_OFF:
            self._set_guests.execute(
                SetGuestsInputDTO(nights=0, now=now, clock_trusted=clock_trusted, source=CommandSource.TELEGRAM),
            )
            return str(GUESTS_OFF)

        if command.kind is CommandKind.UNKNOWN_ROOM:
            return str(UNKNOWN_ROOM) % {'rooms': room_names(room.name for room in command.candidates)}

        if command.kind is CommandKind.AMBIGUOUS:
            return str(AMBIGUOUS) % {'rooms': either_of(room.name for room in command.candidates)}

        if command.room is None:
            return str(HELP)

        try:
            return self._room_command(command, command.room, now, clock_trusted=clock_trusted)
        except (RoomNotFoundError, ZoneNotFoundError):
            return self._unknown_room(now, clock_trusted=clock_trusted)

    def _status(self, now: datetime, *, clock_trusted: bool) -> str:
        rooms = self._rooms.rooms(now, clock_trusted=clock_trusted)
        guests = [room for room in rooms if room.intent.reason is IntentReason.GUESTS]
        until = max((room.intent.until for room in guests if room.intent.until is not None), default=None)
        lines = [] if until is None else [_guests_line(until, guests, now)]
        for room in rooms:
            if room.zones or room.air is not None:
                lines.append(room_line(room, now))
                lines.extend(zone_state_line(zone) for zone in room.zones)
        phases = self._phases.phases()
        if phases is not None:
            lines.append(phases_line(phases))
        return '\n'.join(lines) if lines else str(NO_ROOMS)

    def _fireplace(self, lit: bool, now: datetime) -> str:
        try:
            output = self._mark_fireplace.execute(
                MarkFireplaceInputDTO(room=None, lit=lit, now=now, source=CommandSource.TELEGRAM),
            )
        except FireplaceNotFoundError:
            return str(NO_FIREPLACE)

        if output.until is not None:
            return str(FIREPLACE_LIT) % {'until': moment_text(output.until, now)}

        return str(FIREPLACE_OUT if output.changed else FIREPLACE_NOT_MARKED)

    def _guests(self, nights: int, now: datetime, *, clock_trusted: bool) -> str:
        if not 1 <= nights <= MAX_GUEST_NIGHTS:
            return str(NIGHTS)

        guest_rooms = self._guest_rooms(now, clock_trusted=clock_trusted)
        if not guest_rooms:
            return str(NO_GUEST_ROOMS)

        try:
            until = self._set_guests.execute(
                SetGuestsInputDTO(nights=nights, now=now, clock_trusted=clock_trusted, source=CommandSource.TELEGRAM),
            ).until
        except SettingsRejectedError:
            return str(NIGHTS)
        except ClockUntrustedError:
            return str(GUESTS_CLOCK)

        rooms = self._changed({room.slug for room in guest_rooms}, now, clock_trusted=clock_trusted)
        lines = [] if until is None else [_guests_line(until, guest_rooms, now)]
        return '\n'.join([*lines, *zone_write_lines(rooms, now)])

    def _room_command(self, command: OwnerCommand, room: CommandRoom, now: datetime, *, clock_trusted: bool) -> str:
        if command.kind is CommandKind.AUTO:
            return self._auto(room, now, clock_trusted=clock_trusted)

        status = self._room(room.slug, now, clock_trusted=clock_trusted)
        if status is None:
            return self._unknown_room(now, clock_trusted=clock_trusted)

        if command.kind is CommandKind.BOOST:
            return self._boost(status, BOOST, now, clock_trusted=clock_trusted)

        if not status.adjustable:
            return self._not_adjustable(command, status, now, clock_trusted=clock_trusted)

        return self._target(command, status, now, clock_trusted=clock_trusted)

    def _target(self, command: OwnerCommand, status: RoomStatus, now: datetime, *, clock_trusted: bool) -> str:
        try:
            output = self._override_room.execute(
                OverrideRoomInputDTO(
                    room=status.slug,
                    change=CHANGES[command.kind],
                    value=command.value,
                    now=now,
                    clock_trusted=clock_trusted,
                    source=CommandSource.TELEGRAM,
                ),
            )
        except SettingsRejectedError:
            return str(RANGE) % {
                'low': setpoint_degrees(OVERRIDE_TARGET_MIN),
                'high': setpoint_degrees(OVERRIDE_TARGET_MAX),
            }
        except RoomNotAdjustableError:
            return self._not_adjustable(command, status, now, clock_trusted=clock_trusted)

        reply = str(OVERRIDE) % {
            'room': output.name,
            'target': degrees(output.target),
            'until': moment_text(output.until, now),
        }
        return self._with_floors(reply, status.slug, now, clock_trusted=clock_trusted)

    def _not_adjustable(self, command: OwnerCommand, status: RoomStatus, now: datetime, *, clock_trusted: bool) -> str:
        if not status.zones:
            return str(NOTHING) % {'room': status.name}

        if command.kind is CommandKind.WARMER:
            return self._boost(status, BOOST_FOR_COLD, now, clock_trusted=clock_trusted)

        return str(NO_AIR) % {'room': status.name}

    def _boost(self, status: RoomStatus, template: Promise, now: datetime, *, clock_trusted: bool) -> str:
        if not status.zones:
            return str(NO_FLOOR) % {'room': status.name}

        zones = tuple(zone.slug for zone in status.zones)
        until = self._boost_zones.execute(BoostZonesInputDTO(zones=zones, now=now)).until
        reply = str(template) % {'room': status.name, 'until': moment_text(until, now)}
        return self._with_floors(reply, status.slug, now, clock_trusted=clock_trusted)

    def _auto(self, room: CommandRoom, now: datetime, *, clock_trusted: bool) -> str:
        cleared = self._clear_override.execute(
            ClearRoomOverrideInputDTO(room=room.slug, now=now, source=CommandSource.TELEGRAM),
        ).cleared
        if not cleared:
            return str(AUTO_NONE) % {'room': room.name}

        status = next(iter(self._changed({room.slug}, now, clock_trusted=clock_trusted)), None)
        if status is None:
            return str(AUTO_DONE) % {'room': room.name}

        values = {'room': status.name, 'target': degrees(status.intent.target)}
        until = status.intent.until
        reply = str(AUTO_OPEN) % values if until is None else str(AUTO) % {**values, 'until': moment_text(until, now)}
        return '\n'.join([reply, *zone_write_lines([status], now)])

    def _with_floors(self, reply: str, slug: str, now: datetime, *, clock_trusted: bool) -> str:
        return '\n'.join([reply, *zone_write_lines(self._changed({slug}, now, clock_trusted=clock_trusted), now)])

    def _changed(self, slugs: Collection[str], now: datetime, *, clock_trusted: bool) -> list[RoomStatus]:
        try:
            rooms = self._rooms.rooms(now, clock_trusted=clock_trusted)
        except OperationalError:
            return []

        return [room for room in rooms if room.slug in slugs]

    def _unknown_room(self, now: datetime, *, clock_trusted: bool) -> str:
        names = room_names(room.name for room in self._rooms.rooms(now, clock_trusted=clock_trusted))
        return str(UNKNOWN_ROOM) % {'rooms': names}

    def _room(self, slug: str, now: datetime, *, clock_trusted: bool) -> RoomStatus | None:
        return next((room for room in self._rooms.rooms(now, clock_trusted=clock_trusted) if room.slug == slug), None)

    def _guest_rooms(self, now: datetime, *, clock_trusted: bool) -> list[RoomStatus]:
        return [room for room in self._rooms.rooms(now, clock_trusted=clock_trusted) if room.guest_room]


def _guests_line(until: datetime, rooms: Sequence[RoomStatus], now: datetime) -> str:
    return str(GUESTS_SET) % {'until': moment_text(until, now), 'rooms': room_names(room.name for room in rooms)}
