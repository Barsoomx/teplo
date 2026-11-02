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
