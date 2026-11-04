from enum import StrEnum


class ZoneKind(StrEnum):
    AIR = 'air'
    FLOOR = 'floor'


class ScheduleMode(StrEnum):
    COMFORT = 'comfort'
    PREHEAT = 'preheat'
    NIGHT = 'night'


class AirState(StrEnum):
    HEAT = 'heat'
    IDLE = 'idle'


class ControlPath(StrEnum):
    FLOOR = 'floor'
    AIR = 'air'
    AIR_DRY_RUN = 'air_dry_run'


class CommandSource(StrEnum):
    PANEL = 'panel'
    TELEGRAM = 'telegram'


class ZoneCycleOutcome(StrEnum):
    UNCHANGED = 'unchanged'
    APPLIED = 'applied'
    DRY_RUN = 'dry_run'
    THROTTLED = 'throttled'
    DEVICE_UNAVAILABLE = 'device_unavailable'
    FAILED = 'failed'
    SHED = 'shed'
    HANDS_OFF = 'hands_off'
    WAITING = 'waiting'


FOLLOWED = frozenset({ZoneCycleOutcome.UNCHANGED, ZoneCycleOutcome.APPLIED, ZoneCycleOutcome.DRY_RUN})
UNREACHED = frozenset({ZoneCycleOutcome.DEVICE_UNAVAILABLE, ZoneCycleOutcome.FAILED})
