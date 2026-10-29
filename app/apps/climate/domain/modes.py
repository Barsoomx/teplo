from enum import StrEnum


class ZoneKind(StrEnum):
    AIR = 'air'
    FLOOR = 'floor'


class ScheduleMode(StrEnum):
    COMFORT = 'comfort'
    PREHEAT = 'preheat'
    NIGHT = 'night'
