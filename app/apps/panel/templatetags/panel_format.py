from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django import template
from django.template.defaultfilters import floatformat
from django.templatetags.static import static
from django.utils import timezone
from django.utils.dates import WEEKDAYS, WEEKDAYS_ABBR
from django.utils.formats import date_format
from django.utils.functional import Promise
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext, gettext_lazy

from apps.climate.domain.comfort_windows import WEEKDAY_FIELDS
from apps.climate.domain.drive import PendingKind
from apps.climate.domain.external_heat import FireEnd
from apps.climate.domain.heat_sources import HeatSourceKind
from apps.climate.domain.modes import CommandSource, ScheduleMode
from apps.climate.domain.room_climate import ROOM
from apps.climate.domain.room_intent import (
    MAX_GUEST_NIGHTS,
    OVERRIDE_STEP,
    OVERRIDE_TARGET_MAX,
    OVERRIDE_TARGET_MIN,
    RoomIntent,
)
from apps.climate.domain.week_plan import DayPeriod
from apps.devices.domain.kinds import DeviceKind
from apps.devices.domain.readings import ClimateMode
from apps.notifications.domain.alerts import AlertTransition
from apps.notifications.services.messages import alert_text_from_payload, duration_text, interval_parts
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.panel.usecases.get_panel_overview import (
    LoadOverview,
    OccupancyOverview,
    PresenceOverview,
    SourceOverview,
    ZoneOverview,
)
from apps.panel.usecases.get_phase_currents import ESTIMATE_BASE, EstimatePartOverview, PhaseCurrent
from apps.power.domain.loads import MEASURED_PHASES, AmpsOrigin, LoadKind, PhaseOrigin
from apps.power.domain.outage import OutageRecord
from apps.presence.domain.occupancy import KNOWN_OCCUPANCY, Occupancy
from apps.system.domain.clock import ClockStatusCode

register = template.Library()

FAILURE_NOTES = {
    'device_unavailable': gettext_lazy('No connection to the thermostat'),
    'device_protocol_error': gettext_lazy('The thermostat sent an unexpected reply'),
    'driver_not_implemented': gettext_lazy('teplo cannot control this device yet'),
    'device_not_found': gettext_lazy('The device is missing from the settings'),
    'secret_missing': gettext_lazy('The secrets file has no address and key for this thermostat'),
    'device_config_invalid': gettext_lazy('The secrets file gives this thermostat a wrong address or protocol'),
}
PLUG_FAILURE_NOTES = {
    **FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the plug'),
    'device_protocol_error': gettext_lazy('The plug sent an unexpected reply'),
    'secret_missing': gettext_lazy('The secrets file has no address and key for this plug'),
    'device_config_invalid': gettext_lazy('The secrets file gives this plug a wrong address or protocol'),
    'plug_not_switchable': gettext_lazy('The driver of this plug cannot switch it'),
}
BROKER_UNAVAILABLE_NOTE = gettext_lazy('teplo cannot connect to the MQTT broker')
SENSOR_FAILURE_NOTES = {
    **FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the sensor'),
    'device_protocol_error': gettext_lazy('The sensor sent an unexpected reply'),
    'sensor_reading_failed': gettext_lazy('The sensor could not take a reading'),
    'broker_unavailable': BROKER_UNAVAILABLE_NOTE,
}
PHASE_METER_FAILURE_NOTES = {
    **SENSOR_FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the phase meter'),
    'device_protocol_error': gettext_lazy('The phase meter sent an unexpected reply'),
    'sensor_reading_failed': gettext_lazy('The phase meter could not measure a phase'),
}
CLIMATE_UNIT_FAILURE_NOTES = {
    **FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the unit'),
    'device_protocol_error': gettext_lazy('The unit sent an unexpected reply'),
    'secret_missing': gettext_lazy('The secrets file has no address and key for this unit'),
    'device_config_invalid': gettext_lazy('The secrets file gives this unit a wrong address or protocol'),
    'climate_unit_not_controllable': gettext_lazy('The driver of this unit cannot command it'),
    'broker_unavailable': BROKER_UNAVAILABLE_NOTE,
    'sensor_reading_failed': gettext_lazy('The unit reported no target'),
}
PRESENCE_FAILURE_NOTES = {
    **SENSOR_FAILURE_NOTES,
    'sensor_reading_failed': gettext_lazy('The radar sends no frames'),
    'device_protocol_error': gettext_lazy('The presence source sent a value other than 0 or 1'),
}
DEVICE_FAILURE_NOTES = {
    DeviceKind.PLUG.value: PLUG_FAILURE_NOTES,
    DeviceKind.TEMPERATURE_SENSOR.value: SENSOR_FAILURE_NOTES,
    DeviceKind.PHASE_METER.value: PHASE_METER_FAILURE_NOTES,
    DeviceKind.CLIMATE_UNIT.value: CLIMATE_UNIT_FAILURE_NOTES,
    DeviceKind.PRESENCE_SENSOR.value: PRESENCE_FAILURE_NOTES,
}
UNKNOWN_FAILURE = gettext_lazy('Unknown error')
CLOCK_NOTES = {
    ClockStatusCode.NTP_STATUS_MISSING: gettext_lazy('the router reports no NTP status'),
    ClockStatusCode.NTP_STATUS_FAILED: gettext_lazy('could not read the NTP status from the router'),
}
ALERT_TRANSITIONS = {'alert_raised': AlertTransition.RAISED, 'alert_cleared': AlertTransition.CLEARED}
OUTAGE_QUESTION = gettext_lazy('Power was out %(began)s–%(ended)s (%(duration)s). What was it?')
POWER_OUTAGE = gettext_lazy('Power was out %(began)s–%(ended)s (%(duration)s)')
UNKNOWN_EVENT = gettext_lazy('%(name)s: unknown event')
AIR_CONTROL = {
    'heat': gettext_lazy('%(name)s: air mode would start heating (%(air)s° against a target of %(target)s°)'),
    'idle': gettext_lazy('%(name)s: air mode would stop heating (%(air)s° against a target of %(target)s°)'),
}
AIR_CONTROL_RESUMED = {
    'heat': gettext_lazy(
        '%(name)s: watching air mode again, it would be heating (%(air)s° against a target of %(target)s°)',
    ),
    'idle': gettext_lazy(
        '%(name)s: watching air mode again, it would not be heating (%(air)s° against a target of %(target)s°)',
    ),
}
AIR_CONTROL_SETPOINT = gettext_lazy('%(name)s: air mode would hold the thermostat at %(setpoint)s°')
AIR_CONTROL_OFF = gettext_lazy('%(name)s: air mode would turn the thermostat off')
WOULD_BE_SETPOINT = gettext_lazy('Thermostat would be set to %(setpoint)s°')
WOULD_BE_OFF = gettext_lazy('Thermostat would be off')
OPTIMAL_START = gettext_lazy('%(name)s: air mode would start the warm-up at %(start)s instead of %(fixed)s')
SAME_OPTIMAL_START = gettext_lazy('%(name)s: air mode would also start the warm-up at %(start)s')
GUARD_ON = gettext_lazy('%(name)s: the second-floor guard would turn comfort on (second floor %(air)s°)')
GUARD_OFF = gettext_lazy(
    '%(name)s: the second-floor guard would return the zone to its schedule (second floor %(air)s°)',
)
GUARD_OFF_UNREAD = gettext_lazy(
    '%(name)s: the second-floor guard would return the zone to its schedule (no second-floor reading)',
)
BACKUP_ON = gettext_lazy('%(name)s: backup heat would turn comfort on (%(room)s %(air)s°)')
BACKUP_OFF = gettext_lazy('%(name)s: backup heat would return the zone to its schedule (%(room)s %(air)s°)')
BACKUP_OFF_UNREAD = gettext_lazy('%(name)s: backup heat would return the zone to its schedule (no reading in %(room)s)')
NEXT_WRITE_AT = gettext_lazy('Thermostat set to %(setpoint)s°, %(target)s° no sooner than %(time)s')
NEXT_WRITE_SOON = gettext_lazy('Thermostat set to %(setpoint)s°, %(target)s° within the next minute')
HANDS_OFF_SETPOINT = gettext_lazy('Thermostat set to %(setpoint)s°: teplo returns to the schedule after %(time)s')
HANDS_OFF_POWER_OFF = gettext_lazy('Thermostat is off: teplo returns to the schedule after %(time)s')
MANUAL_CHANGE = gettext_lazy('%(name)s: changed on the thermostat: %(changes)s, teplo leaves it alone until %(time)s')
MANUAL_CHANGE_DRY_RUN = gettext_lazy(
    '%(name)s: changed on the thermostat: %(changes)s, teplo would leave it alone until %(time)s',
)
SOURCE_MANUAL_CHANGE = gettext_lazy(
    '%(name)s: changed on the unit: %(changes)s, teplo leaves it alone until %(time)s',
)
SOURCE_MANUAL_CHANGE_DRY_RUN = gettext_lazy(
    '%(name)s: changed on the unit: %(changes)s, teplo would leave it alone until %(time)s',
)
SOURCE_COMMANDS = {
    (False, True, True): gettext_lazy('%(name)s: switched on at %(setpoint)s°'),
    (False, True, False): gettext_lazy('%(name)s: switched on'),
    (False, False, False): gettext_lazy('%(name)s: switched off'),
    (False, None, True): gettext_lazy('%(name)s: set %(setpoint)s°'),
    (True, True, True): gettext_lazy('%(name)s: would switch on at %(setpoint)s°'),
    (True, True, False): gettext_lazy('%(name)s: would switch on'),
    (True, False, False): gettext_lazy('%(name)s: would switch off'),
    (True, None, True): gettext_lazy('%(name)s: would set %(setpoint)s°'),
}
SOURCE_COMMANDS_FAILED = {
    (True, True): gettext_lazy('%(name)s: could not switch on at %(setpoint)s°'),
    (True, False): gettext_lazy('%(name)s: could not switch on'),
    (False, False): gettext_lazy('%(name)s: could not switch off'),
    (None, True): gettext_lazy('%(name)s: could not set %(setpoint)s°'),
}
ANY_SOURCE_COMMAND_FAILED = gettext_lazy('%(name)s: a command failed')
SOURCE_HEATS_UP_TO = gettext_lazy('heats up to %(setpoint)s°')
SOURCE_MODE_STATES = {
    ClimateMode.HEAT: SOURCE_HEATS_UP_TO,
    ClimateMode.COOL: gettext_lazy('cools down to %(setpoint)s°'),
    ClimateMode.AUTO: gettext_lazy('auto, %(setpoint)s°'),
    ClimateMode.OTHER: gettext_lazy('another mode, %(setpoint)s°'),
}
SOURCE_IS_OFF = gettext_lazy('the unit is off')
SOURCE_OWNER_MODE = gettext_lazy('The owner’s mode')
SOURCE_FROST_LOCKOUT = gettext_lazy('Frost lockout: %(temperature)s° outside')
SOURCE_LOCKED_WITHOUT_OUTDOOR = gettext_lazy('Locked out: no outdoor reading')
MODE_WORDS = {
    ClimateMode.HEAT.value: gettext_lazy('heating'),
    ClimateMode.COOL.value: gettext_lazy('cooling'),
    ClimateMode.AUTO.value: gettext_lazy('auto'),
    ClimateMode.OTHER.value: gettext_lazy('another mode'),
    ClimateMode.OFF.value: gettext_lazy('off'),
}
SOURCE_OWN_TEMPERATURE = gettext_lazy('%(temperature)s° at the unit')
SOURCE_STATE_SEPARATOR = ' · '
SOURCE_SETS_AT = gettext_lazy('teplo sets %(setpoint)s° no sooner than %(time)s')
SOURCE_SETS_SOON = gettext_lazy('teplo sets %(setpoint)s° within the next minute')
SOURCE_OFF_AT = gettext_lazy('teplo switches it off no sooner than %(time)s')
SOURCE_OFF_SOON = gettext_lazy('teplo switches it off within the next minute')
MANUAL_POWER = {True: gettext_lazy('switched on'), False: gettext_lazy('switched off')}
MANUAL_MODE = gettext_lazy('mode %(old)s → %(new)s')
MANUAL_SETPOINT = '%(old)s → %(new)s'
MANUAL_CHANGES_SEPARATOR = ', '
GUESTS_UNTIL = gettext_lazy('Guests until %(time)s')
GUESTS_OFF = gettext_lazy('Guests mode is off')
GUEST_NIGHTS = {1: gettext_lazy('1 night'), 2: gettext_lazy('2 nights'), 3: gettext_lazy('3 nights')}
ROOM_OVERRIDDEN = gettext_lazy('%(name)s: manual %(target)s° until %(time)s')
ROOM_OVERRIDE_CLEARED = gettext_lazy('%(name)s: back to the schedule')
ROOM_OVERRIDE_CLEARED_OCCUPANCY = gettext_lazy('%(name)s: back to occupancy')
OCCUPANCY_TEXTS = {
    Occupancy.OCCUPIED: gettext_lazy('%(name)s: occupied'),
    Occupancy.VACANT: gettext_lazy('%(name)s: nobody there'),
    Occupancy.UNKNOWN: gettext_lazy('%(name)s: no presence data'),
}
OCCUPANCY_WATCHED = gettext_lazy('Watching: heating keeps the schedule')
OCCUPANCY_NOTES = {Occupancy.OCCUPIED: gettext_lazy('Occupied'), Occupancy.VACANT: gettext_lazy('Nobody there')}
OCCUPANCY_WOULD_HOLD = gettext_lazy('%(name)s: by occupancy would hold %(setpoint)s°')
OCCUPANCY_WOULD_SWITCH_OFF = gettext_lazy('%(name)s: by occupancy would switch off')
SOMEONE_MINUTES_AGO = gettext_lazy('%(name)s: someone %(minutes)s min ago')
NOBODY_SEEN = gettext_lazy('%(name)s: nobody seen')
PRESENCE_NO_CONNECTION = gettext_lazy('%(name)s: no connection')
OCCUPANCY_SOURCES_SEPARATOR = ' · '
FROM_TELEGRAM = gettext_lazy('From Telegram')
FIREPLACE_MARKED = gettext_lazy('%(name)s: lit until %(time)s')
FIREPLACE_MARK_CLEARED = gettext_lazy('%(name)s: out')
EXTERNAL_HEAT_STARTED = gettext_lazy('%(name)s: looks like the fireplace is lit (+%(rate)s°/h)')
EXTERNAL_HEAT_ENDED = gettext_lazy('%(name)s: looks like the fireplace has burnt out')
EXTERNAL_HEAT_WATCHED = gettext_lazy('Watching: learning goes on')
EXTERNAL_HEAT_TIMED_OUT = gettext_lazy('Six hours have passed')
MORNING_REJECTED = gettext_lazy('%(name)s: morning not learned: %(rate)s°/h is faster than the cap of %(cap)s°/h')
LEARNING_RESET = gettext_lazy('%(name)s: learning reset')
LEARNED_BEFORE = gettext_lazy('Was: %(values)s')
LEARNED_MORNINGS = gettext_lazy('mornings %(count)s')
LEARNED_DEAD_TIME = gettext_lazy('delay %(minutes)s min')
LEARNED_EARLY_STOP = gettext_lazy('early stop %(offset)s°')
OWNER_COMMAND = gettext_lazy('Telegram: “%(text)s”')
PHASE_LABELS = {phase.value: phase.value.upper() for phase in MEASURED_PHASES}
LOAD_PHASES = {
    PhaseOrigin.SET: gettext_lazy('Phase %(phase)s, set'),
    PhaseOrigin.LEARNED: gettext_lazy('Phase %(phase)s, learned'),
}
UNKNOWN_LOAD_PHASE = gettext_lazy('Phase unknown')
LOAD_AMPS = {
    AmpsOrigin.NOMINAL: gettext_lazy('%(amps)s A, nominal'),
    AmpsOrigin.LEARNED: gettext_lazy('%(amps)s A, learned'),
    AmpsOrigin.RATED: gettext_lazy('%(amps)s A by its rated power'),
    AmpsOrigin.DEFAULT: gettext_lazy('%(amps)s A by default'),
}
PHASE_OVERLOAD = gettext_lazy('Overload on phase %(phase)s: %(amps)s A')
ESTIMATED_PHASE_OVERLOAD = gettext_lazy('Overload on phase %(phase)s by the estimate: %(amps)s A')
PHASE_EVENTS = {
    'phase_overload': PHASE_OVERLOAD,
    'phase_calm': gettext_lazy('Overload on phase %(phase)s is over: %(amps)s A'),
    'ev_charging_started': gettext_lazy('Phase %(phase)s: an EV seems to be charging, +%(amps)s A'),
}
ESTIMATED_PHASE_EVENTS = {
    'phase_overload': ESTIMATED_PHASE_OVERLOAD,
    'phase_calm': gettext_lazy('Overload on phase %(phase)s by the estimate is over: %(amps)s A'),
}
EV_CHARGING_STOPPED = gettext_lazy('Phase %(phase)s: the EV is no longer charging')
LOAD_SHEDS = {
    ('load_shed', LoadKind.PLUG): gettext_lazy('%(name)s: switched the plug off'),
    ('load_shed', LoadKind.ZONE): gettext_lazy('%(name)s: switched the thermostat off'),
    ('load_shed_dry_run', LoadKind.PLUG): gettext_lazy('%(name)s: would switch the plug off'),
    ('load_shed_dry_run', LoadKind.ZONE): gettext_lazy('%(name)s: would switch the thermostat off'),
    ('load_restored', LoadKind.PLUG): gettext_lazy('%(name)s: switched the plug back on'),
    ('load_restored', LoadKind.ZONE): gettext_lazy('%(name)s: handed the thermostat back to its schedule'),
    ('load_restored_dry_run', LoadKind.PLUG): gettext_lazy('%(name)s: would switch the plug back on'),
    ('load_restored_dry_run', LoadKind.ZONE): gettext_lazy(
        '%(name)s: would hand the thermostat back to its schedule',
    ),
    ('load_shed', LoadKind.SOURCE): gettext_lazy('%(name)s: switched the heater off'),
    ('load_shed_dry_run', LoadKind.SOURCE): gettext_lazy('%(name)s: would switch the heater off'),
    ('load_restored', LoadKind.SOURCE): gettext_lazy('%(name)s: handed the heater back to its room'),
    ('load_restored_dry_run', LoadKind.SOURCE): gettext_lazy('%(name)s: would hand the heater back to its room'),
}
SWITCHED_BACK_ON = {
    LoadKind.ZONE: gettext_lazy('%(name)s: switched the thermostat back on'),
    LoadKind.SOURCE: gettext_lazy('%(name)s: switched the heater back on'),
}
SHED_UNMEASURED_OVERLOAD = gettext_lazy('Overload on phase %(phase)s')
ESTIMATED_SHED_UNMEASURED_OVERLOAD = gettext_lazy('Overload on phase %(phase)s by the estimate')
SHED_ON_METER_LOST = gettext_lazy('No readings from the phase meter: one floor per phase')
RESTORED_ON_CALM = gettext_lazy('Phase %(phase)s back to normal: %(amps)s A')
ESTIMATED_RESTORED_ON_CALM = gettext_lazy('Phase %(phase)s back to normal by the estimate: %(amps)s A')
RESTORED_ON_CALM_PHASES = gettext_lazy('Phases back to normal')
RESTORED_ON_RELEASE = gettext_lazy('Load management was switched in the settings')
RESTORED_WHILE_METER_LOST = gettext_lazy('No readings from the phase meter: no other floor heats on this phase')
LOAD_COMMANDS_FAILED = {
    ('shed', LoadKind.PLUG): gettext_lazy('%(name)s: could not switch the plug off'),
    ('restore', LoadKind.PLUG): gettext_lazy('%(name)s: could not switch the plug back on'),
    ('shed', LoadKind.ZONE): gettext_lazy('%(name)s: could not switch the thermostat off'),
    ('restore', LoadKind.ZONE): gettext_lazy('%(name)s: could not switch the thermostat back on'),
    ('shed', LoadKind.SOURCE): gettext_lazy('%(name)s: could not switch the heater off'),
    ('restore', LoadKind.SOURCE): gettext_lazy('%(name)s: could not switch the heater back on'),
}
ANY_LOAD_COMMAND_FAILED = gettext_lazy('%(name)s: a load management command failed')
START_WOULD_WAIT = gettext_lazy('%(name)s: would put off switching on')
START_WAITS_FOR_ROOM = {
    'no_headroom': gettext_lazy('Phase %(phase)s would reach %(amps)s A'),
    'phase_would_overload': gettext_lazy(
        'The phase meter is silent and phase %(phase)s would reach %(amps)s A by its last reading',
    ),
}
ESTIMATED_START_WAITS_FOR_ROOM = {
    'no_headroom': gettext_lazy('Phase %(phase)s would reach %(amps)s A by the estimate'),
    'phase_would_overload': gettext_lazy(
        'The phase meter is silent and phase %(phase)s would reach %(amps)s A by its last estimate',
    ),
}
START_REFUSALS = {
    'too_soon': gettext_lazy('Another load has just started'),
    'meter_stale': gettext_lazy('No fresh readings from the phase meter'),
    'phase_taken': gettext_lazy('The phase meter is silent and another floor already heats on this phase'),
    'phase_overloaded': gettext_lazy('The phase meter is silent and the phase was overloaded when it went silent'),
    'phase_not_calm': gettext_lazy(
        'The phase meter is silent and the phase was above the restore threshold when it went silent',
    ),
}
LOAD_PHASE_LEARNED = gettext_lazy('%(name)s: learned phase %(phase)s, %(amps)s A')
OVERLOAD_BEFORE_CUT = gettext_lazy('Phase %(phase)s was overloaded right before the cut: %(amps)s A')
UNMEASURED_OVERLOAD_BEFORE_CUT = gettext_lazy('Phase %(phase)s was overloaded right before the cut')
ANY_OVERLOAD_BEFORE_CUT = gettext_lazy('A phase was overloaded right before the cut')
ESTIMATE_BASE_NAME = gettext_lazy('base')
ESTIMATE_PART = gettext_lazy('%(name)s %(amps)s A')
ESTIMATE_PARTS_SEPARATOR = ' · '
DEVICE_LOAD_KINDS = {
    DeviceKind.PLUG: LoadKind.PLUG,
    DeviceKind.THERMOSTAT: LoadKind.ZONE,
    DeviceKind.CLIMATE_UNIT: LoadKind.SOURCE,
}
DAY_PERIODS = {
    ScheduleMode.COMFORT: gettext_lazy('Comfort %(start)s–%(end)s'),
    ScheduleMode.PREHEAT: gettext_lazy('Warm-up %(start)s–%(end)s'),
    ScheduleMode.NIGHT: gettext_lazy('Night %(start)s–%(end)s'),
}
SETTINGS_SUBJECTS = {
    'climate_settings': gettext_lazy('Climate'),
    'load_settings': gettext_lazy('Load management'),
}
SETTING_CHANGED = gettext_lazy('%(name)s: %(field)s %(old)s → %(new)s')
SETTINGS_CHANGED = gettext_lazy('%(name)s: settings changed')
ZONE_RENAMED = gettext_lazy('%(name)s: renamed, was “%(old)s”')
HEATING_SWITCHED = {
    True: gettext_lazy('%(name)s: heating switched on'),
    False: gettext_lazy('%(name)s: heating switched off'),
}
GUEST_ROOM_SWITCHED = {
    True: gettext_lazy('%(name)s: a guest room now'),
    False: gettext_lazy('%(name)s: no longer a guest room'),
}
SCHEDULE_CHANGES = dict(
    zip(
        WEEKDAY_FIELDS,
        (
            gettext_lazy('%(name)s, Monday: comfort %(old)s → %(new)s'),
            gettext_lazy('%(name)s, Tuesday: comfort %(old)s → %(new)s'),
            gettext_lazy('%(name)s, Wednesday: comfort %(old)s → %(new)s'),
            gettext_lazy('%(name)s, Thursday: comfort %(old)s → %(new)s'),
            gettext_lazy('%(name)s, Friday: comfort %(old)s → %(new)s'),
            gettext_lazy('%(name)s, Saturday: comfort %(old)s → %(new)s'),
            gettext_lazy('%(name)s, Sunday: comfort %(old)s → %(new)s'),
        ),
        strict=True,
    ),
)
NO_COMFORT = gettext_lazy('none')
UNKNOWN_PHASE = gettext_lazy('not known')
NOT_SET = gettext_lazy('not set')


def _degrees(value: object) -> str:
    return gettext('%(temperature)s°') % {'temperature': floatformat(value, -1)}


def _fine_degrees(value: object) -> str:
    return gettext('%(temperature)s°') % {'temperature': floatformat(value, -2)}


def _amps(value: object) -> str:
    return gettext('%(amps)s A') % {'amps': floatformat(value, -1)}


def _optional_amps(value: object) -> str:
    return str(NOT_SET) if value is None else _amps(value)


def _seconds(value: object) -> str:
    return gettext('%(seconds)s s') % {'seconds': value}


def _minutes(value: object) -> str:
    return duration_text(timedelta(minutes=value)) if isinstance(value, int) else str(value)


def _number(value: object) -> str:
    return str(value)


def _phase(value: object) -> str:
    return PHASE_LABELS.get(str(value), str(UNKNOWN_PHASE))


def _hour(value: object) -> str:
    return f'{value:02d}:00' if isinstance(value, int) and not isinstance(value, bool) else str(value)


SETTINGS_FIELDS: dict[tuple[str, str], tuple[Promise, Callable[[object], str]]] = {
    ('zone', 'comfort_floor_setpoint'): (gettext_lazy('floor in comfort'), _degrees),
    ('zone', 'night_floor_setpoint'): (gettext_lazy('floor at night'), _degrees),
    ('zone', 'boost_floor_setpoint'): (gettext_lazy('floor in boost'), _degrees),
    ('zone', 'idle_floor_setpoint'): (gettext_lazy('idle floor in air mode'), _degrees),
    ('zone', 'floor_limit_setpoint'): (gettext_lazy('floor limit in air mode'), _degrees),
    ('zone', 'comfort_air_target'): (gettext_lazy('air in comfort'), _degrees),
    ('zone', 'night_air_target'): (gettext_lazy('air at night'), _degrees),
    ('zone', 'preheat_lead_minutes'): (gettext_lazy('warm-up lead'), _minutes),
    ('climate_settings', 'air_start_offset'): (gettext_lazy('heat below the target by'), _fine_degrees),
    ('climate_settings', 'initial_early_stop_offset'): (gettext_lazy('first early stop'), _fine_degrees),
    ('climate_settings', 'air_min_band'): (gettext_lazy('minimum band between start and stop'), _fine_degrees),
    ('climate_settings', 'learned_mornings_needed'): (gettext_lazy('learned mornings needed'), _number),
    ('climate_settings', 'preheat_lead_cap_minutes'): (gettext_lazy('longest warm-up'), _minutes),
    ('climate_settings', 'guests_end_hour'): (gettext_lazy('guests until'), _hour),
    ('climate_settings', 'second_floor_guard_threshold'): (gettext_lazy('second-floor guard below'), _degrees),
    ('climate_settings', 'second_floor_guard_hysteresis'): (
        gettext_lazy('second-floor guard hysteresis'),
        _fine_degrees,
    ),
    ('room', 'comfort_target'): (gettext_lazy('air in comfort'), _degrees),
    ('room', 'setback_target'): (gettext_lazy('air at night'), _degrees),
    ('room', 'backup_below'): (gettext_lazy('backup heat below'), _degrees),
    ('room', 'backup_hysteresis'): (gettext_lazy('backup heat hysteresis'), _fine_degrees),
    ('load_settings', 'start_ceiling_amps'): (gettext_lazy('start ceiling'), _amps),
    ('load_settings', 'shed_amps'): (gettext_lazy('shed threshold'), _amps),
    ('load_settings', 'shed_hold_seconds'): (gettext_lazy('shed after'), _seconds),
    ('load_settings', 'restore_amps'): (gettext_lazy('restore threshold'), _amps),
    ('load_settings', 'restore_hold_seconds'): (gettext_lazy('restore after'), _seconds),
    ('load_settings', 'start_interval_seconds'): (gettext_lazy('starts apart'), _seconds),
    ('load_settings', 'meter_lost_after_seconds'): (gettext_lazy('meter lost after'), _seconds),
    ('load_settings', 'default_load_amps'): (gettext_lazy('load current by default'), _amps),
    ('load', 'priority'): (gettext_lazy('priority'), _number),
    ('load', 'phase'): (gettext_lazy('phase'), _phase),
    ('load', 'nominal_amps'): (gettext_lazy('nominal current'), _optional_amps),
}


@register.simple_tag
def icon(name: str) -> SafeString:
    return format_html(
        '<svg class="icon" aria-hidden="true" focusable="false"><use href="{}#{}"></use></svg>',
        static('panel/icons.svg'),
        name,
    )


@register.filter
def command_summary(change: dict[str, object]) -> str:
    parts: list[str] = []
    if change.get('power') is True:
        parts.append(gettext('turn on'))
    elif change.get('power') is False:
        parts.append(gettext('turn off'))
    if change.get('mode') == 'hold':
        parts.append(gettext('manual mode'))
    setpoint = change.get('setpoint')
    if setpoint is not None:
        parts.append(gettext('set to %(temperature)s°') % {'temperature': floatformat(setpoint, -1)})
    return ', '.join(parts)


@register.simple_tag
def failure_note(error_code: str | None, error: str | None, omit: str = '', device: str | None = None) -> str:
    if not error_code:
        return str(UNKNOWN_FAILURE) if error else ''

    if error_code == omit:
        return ''

    notes = FAILURE_NOTES if device is None else DEVICE_FAILURE_NOTES.get(device, FAILURE_NOTES)
    return str(notes.get(error_code, UNKNOWN_FAILURE))


@register.simple_tag
def clock_note(code: ClockStatusCode) -> str:
    return str(CLOCK_NOTES.get(code, ''))


@register.filter
def local_time(value: str) -> str:
    return timezone.localtime(datetime.fromisoformat(value)).strftime('%H:%M')


@register.filter
def duration(value: timedelta) -> str:
    return duration_text(value)


@register.filter
def day_word(value: datetime) -> str:
    day = timezone.localdate(value)
    today = timezone.localdate()
    if day == today:
        return ''

    if day == today + timedelta(days=1):
        return gettext('tomorrow')

    return date_format(day, 'j E')


@register.filter
def next_write_text(zone: ZoneOverview) -> str:
    pending = zone.pending
    if pending is not None and pending.kind is PendingKind.HANDS_OFF and pending.at is not None:
        return _hands_off_text(zone, pending.at)

    if pending is None or pending.kind is not PendingKind.NEXT_WRITE or pending.at is None:
        return ''

    values = {'setpoint': floatformat(zone.setpoint, -1), 'target': floatformat(zone.target_setpoint, -1)}
    if pending.at <= timezone.now():
        return str(NEXT_WRITE_SOON) % values

    return str(NEXT_WRITE_AT) % {**values, 'time': _moment_text(pending.at)}


@register.simple_tag
def guest_nights() -> tuple[tuple[int, str], ...]:
    return tuple((nights, str(GUEST_NIGHTS[nights])) for nights in range(1, MAX_GUEST_NIGHTS + 1))


@register.filter
def steps_down(intent: RoomIntent) -> bool:
    return intent.target - OVERRIDE_STEP >= OVERRIDE_TARGET_MIN


@register.filter
def steps_up(intent: RoomIntent) -> bool:
    return intent.target + OVERRIDE_STEP <= OVERRIDE_TARGET_MAX


@register.filter
def guests_text(entry: TimelineEntry) -> str:
    if entry.kind == 'guests_cleared':
        return str(GUESTS_OFF)

    moment = _journaled_moment(entry.payload.get('until'))
    if moment is None:
        return _unknown_event(entry)

    return str(GUESTS_UNTIL) % {'time': moment}


@register.filter
def room_override_text(entry: TimelineEntry) -> str:
    name = entry.payload.get('name')
    if not isinstance(name, str) or not name:
        return _unknown_event(entry)

    if entry.kind == 'room_override_cleared':
        template = ROOM_OVERRIDE_CLEARED_OCCUPANCY if _occupancy(entry) in KNOWN_OCCUPANCY else ROOM_OVERRIDE_CLEARED
        return str(template) % {'name': name}

    target = floatformat(entry.payload.get('target'), 1)
    moment = _journaled_moment(entry.payload.get('until'))
    if not target or moment is None:
        return _unknown_event(entry)

    return str(ROOM_OVERRIDDEN) % {'name': name, 'target': target, 'time': moment}


@register.filter
def occupancy_sources(occupancy: OccupancyOverview) -> str:
    window = timedelta(minutes=occupancy.vacant_after_minutes).total_seconds()
    return OCCUPANCY_SOURCES_SEPARATOR.join(_presence_source_text(source, window) for source in occupancy.sources)


@register.filter
def occupancy_text(entry: TimelineEntry) -> str:
    name = entry.payload.get('name')
    occupancy = _occupancy(entry)
    if not isinstance(name, str) or not name or occupancy is None:
        return _unknown_event(entry)

    return str(OCCUPANCY_TEXTS[occupancy]) % {'name': name}


@register.filter
def occupancy_note(entry: TimelineEntry) -> str:
    if entry.payload.get('live') is not False or _occupancy(entry) not in KNOWN_OCCUPANCY:
        return ''

    return str(OCCUPANCY_WATCHED)


@register.filter
def occupancy_dry_run_text(entry: TimelineEntry) -> str:
    name = entry.payload.get('name')
    power = entry.payload.get('power')
    setpoint = floatformat(entry.payload.get('setpoint'), -1)
    if not isinstance(name, str) or not name or not isinstance(power, bool) or (power and not setpoint):
        return _unknown_event(entry)

    if not power:
        return str(OCCUPANCY_WOULD_SWITCH_OFF) % {'name': name}

    return str(OCCUPANCY_WOULD_HOLD) % {'name': name, 'setpoint': setpoint}


@register.filter
def occupancy_dry_run_note(entry: TimelineEntry) -> str:
    occupancy = _occupancy(entry)
    if occupancy is None or occupancy not in OCCUPANCY_NOTES:
        return ''

    return str(OCCUPANCY_NOTES[occupancy])


@register.filter
def command_note(entry: TimelineEntry) -> str:
    return str(FROM_TELEGRAM) if entry.payload.get('source') == CommandSource.TELEGRAM else ''


@register.filter
def fireplace_mark_text(entry: TimelineEntry) -> str:
    name = entry.payload.get('name')
    if not isinstance(name, str) or not name:
        return _unknown_event(entry)

    if entry.kind == 'fireplace_mark_cleared':
        return str(FIREPLACE_MARK_CLEARED) % {'name': name}

    moment = _journaled_moment(entry.payload.get('until'))
    if moment is None:
        return _unknown_event(entry)

    return str(FIREPLACE_MARKED) % {'name': name, 'time': moment}


@register.filter
def external_heat_text(entry: TimelineEntry) -> str:
    name = entry.payload.get('room_name')
    if not isinstance(name, str) or not name:
        return _unknown_event(entry)

    if entry.kind == 'external_heat_ended':
        return str(EXTERNAL_HEAT_ENDED) % {'name': name}

    rate = _highest_rate(entry.payload.get('rates'))
    if rate is None:
        return _unknown_event(entry)

    return str(EXTERNAL_HEAT_STARTED) % {'name': name, 'rate': floatformat(rate, 1)}


@register.filter
def external_heat_note(entry: TimelineEntry) -> str:
    if entry.kind == 'external_heat_ended':
        return str(EXTERNAL_HEAT_TIMED_OUT) if entry.payload.get('reason') == FireEnd.TIMEOUT else ''

    return '' if entry.payload.get('live') is True else str(EXTERNAL_HEAT_WATCHED)


@register.filter
def morning_rejected_text(entry: TimelineEntry) -> str:
    rate = floatformat(entry.payload.get('rate'), 1)
    cap = floatformat(entry.payload.get('cap'), 1)
    if not rate or not cap:
        return _unknown_event(entry)

    return str(MORNING_REJECTED) % {'name': _zone_name(entry), 'rate': rate, 'cap': cap}


@register.filter
def learning_reset_text(entry: TimelineEntry) -> str:
    return str(LEARNING_RESET) % {'name': _zone_name(entry)}


@register.filter
def learning_reset_note(entry: TimelineEntry) -> str:
    payload = entry.payload
    values: list[str] = []
    if isinstance(payload.get('mornings'), int):
        values.append(str(LEARNED_MORNINGS) % {'count': payload['mornings']})
    if isinstance(payload.get('dead_time_minutes'), int):
        values.append(str(LEARNED_DEAD_TIME) % {'minutes': payload['dead_time_minutes']})
    offset = floatformat(payload.get('early_stop_offset'), -2)
    if offset:
        values.append(str(LEARNED_EARLY_STOP) % {'offset': offset})
    return str(LEARNED_BEFORE) % {'values': ', '.join(values)} if values else ''


@register.filter
def manual_change_text(entry: TimelineEntry) -> str:
    changes = _manual_changes(entry.payload)
    moment = _journaled_moment(entry.payload.get('until'))
    if not changes or moment is None:
        return _unknown_event(entry)

    template = MANUAL_CHANGE_DRY_RUN if entry.payload.get('dry_run') is True else MANUAL_CHANGE
    return str(template) % {
        'name': _zone_name(entry),
        'changes': MANUAL_CHANGES_SEPARATOR.join(changes),
        'time': moment,
    }


@register.filter
def source_manual_change_text(entry: TimelineEntry) -> str:
    changes = _manual_changes(entry.payload, mode_words=MODE_WORDS)
    moment = _journaled_moment(entry.payload.get('until'))
    if not changes or moment is None:
        return _unknown_event(entry)

    template = SOURCE_MANUAL_CHANGE_DRY_RUN if entry.payload.get('dry_run') is True else SOURCE_MANUAL_CHANGE
    return str(template) % {
        'name': _zone_name(entry),
        'changes': MANUAL_CHANGES_SEPARATOR.join(changes),
        'time': moment,
    }


@register.filter
def source_command_text(entry: TimelineEntry) -> str:
    power = entry.payload.get('power')
    setpoint = floatformat(entry.payload.get('setpoint'), -1)
    template = SOURCE_COMMANDS.get((entry.payload.get('dry_run') is True, _optional_bool(power), bool(setpoint)))
    if template is None:
        return _unknown_event(entry)

    return str(template) % {'name': _zone_name(entry), 'setpoint': setpoint}


@register.filter
def source_command_failed_text(entry: TimelineEntry) -> str:
    setpoint = floatformat(entry.payload.get('setpoint'), -1)
    template = SOURCE_COMMANDS_FAILED.get((_optional_bool(entry.payload.get('power')), bool(setpoint)))
    return str(template or ANY_SOURCE_COMMAND_FAILED) % {'name': _zone_name(entry), 'setpoint': setpoint}


@register.filter
def commanded_sources(sources: tuple[SourceOverview, ...]) -> tuple[SourceOverview, ...]:
    return tuple(source for source in sources if source.kind != HeatSourceKind.FIREPLACE)


@register.filter
def source_state_text(source: SourceOverview) -> str:
    if source.on is None:
        return ''

    parts = [_source_mode_text(source)]
    if source.on and source.level:
        parts.append(source.level)
    if source.temperature is not None:
        parts.append(str(SOURCE_OWN_TEMPERATURE) % {'temperature': floatformat(source.temperature, -1)})
    return SOURCE_STATE_SEPARATOR.join(parts)


@register.filter
def source_owner_mode(source: SourceOverview) -> str:
    return str(SOURCE_OWNER_MODE) if source.on and source.mode is ClimateMode.COOL else ''


@register.filter
def source_lockout_text(source: SourceOverview) -> str:
    if not source.locked_out:
        return ''

    if source.outdoor is None:
        return str(SOURCE_LOCKED_WITHOUT_OUTDOOR)

    return str(SOURCE_FROST_LOCKOUT) % {'temperature': floatformat(source.outdoor, -1)}


@register.filter
def source_pending_text(source: SourceOverview) -> str:
    pending = source.pending
    if pending is None or pending.kind is not PendingKind.NEXT_WRITE or pending.at is None:
        return ''

    soon = pending.at <= timezone.now()
    if not source.target_on:
        return str(SOURCE_OFF_SOON) if soon else str(SOURCE_OFF_AT) % {'time': _moment_text(pending.at)}

    values = {'setpoint': floatformat(source.target_setpoint, -1)}
    if soon:
        return str(SOURCE_SETS_SOON) % values

    return str(SOURCE_SETS_AT) % {**values, 'time': _moment_text(pending.at)}


@register.filter
def owner_command_text(entry: TimelineEntry) -> str:
    text = entry.payload.get('text')
    if not isinstance(text, str) or not text:
        return _unknown_event(entry)

    return str(OWNER_COMMAND) % {'text': text}


@register.filter
def alert_text(entry: TimelineEntry) -> str:
    transition = ALERT_TRANSITIONS.get(entry.kind)
    text = alert_text_from_payload(transition, entry.payload, entry.created_at) if transition is not None else None
    if text is None:
        return _unknown_event(entry)

    return text


@register.filter
def outage_question(outage: OutageRecord) -> str:
    return str(OUTAGE_QUESTION) % interval_parts(outage.began_at, outage.ended_at, timezone.now())


@register.filter
def power_outage_text(entry: TimelineEntry) -> str:
    try:
        parts = interval_parts(
            datetime.fromisoformat(str(entry.payload['began_at'])),
            datetime.fromisoformat(str(entry.payload['ended_at'])),
            entry.created_at,
        )
    except (KeyError, TypeError, ValueError):
        return gettext('Unknown event')

    return str(POWER_OUTAGE) % parts


@register.filter
def air_control_text(entry: TimelineEntry) -> str:
    if _air_state_kept(entry):
        return _air_control_thermostat_text(entry)

    templates = AIR_CONTROL_RESUMED if entry.payload.get('previous_state') is None else AIR_CONTROL
    template = templates.get(str(entry.payload.get('state')))
    air = floatformat(entry.payload.get('air'), 1)
    target = floatformat(entry.payload.get('target'), 1)
    if template is None or not air or not target:
        return _unknown_event(entry)

    return str(template) % {'name': entry.subject_name, 'air': air, 'target': target}


@register.filter
def air_control_note(entry: TimelineEntry) -> str:
    if _air_state_kept(entry):
        return ''

    if entry.payload.get('power') is False:
        return str(WOULD_BE_OFF)

    setpoint = floatformat(entry.payload.get('setpoint'), -1)
    if not setpoint:
        return ''

    return str(WOULD_BE_SETPOINT) % {'setpoint': setpoint}


@register.filter
def optimal_start_text(entry: TimelineEntry) -> str:
    try:
        start = local_time(str(entry.payload['start']))
        fixed = local_time(str(entry.payload['fixed_start']))
    except (KeyError, ValueError):
        return _unknown_event(entry)

    template = SAME_OPTIMAL_START if start == fixed else OPTIMAL_START
    return str(template) % {'name': entry.subject_name, 'start': start, 'fixed': fixed}


@register.filter
def second_floor_guard_text(entry: TimelineEntry) -> str:
    active = entry.payload.get('active')
    air = floatformat(entry.payload.get('air'), 1)
    if active is True and air:
        return str(GUARD_ON) % {'name': entry.subject_name, 'air': air}

    if active is False and air:
        return str(GUARD_OFF) % {'name': entry.subject_name, 'air': air}

    if active is False:
        return str(GUARD_OFF_UNREAD) % {'name': entry.subject_name}

    return _unknown_event(entry)


@register.filter
def backup_heat_text(entry: TimelineEntry) -> str:
    room = entry.payload.get('room_name')
    active = entry.payload.get('active')
    air = floatformat(entry.payload.get('air'), 1)
    if not isinstance(room, str) or not room:
        return _unknown_event(entry)

    if active is True and air:
        return str(BACKUP_ON) % {'name': entry.subject_name, 'room': room, 'air': air}

    if active is False and air:
        return str(BACKUP_OFF) % {'name': entry.subject_name, 'room': room, 'air': air}

    if active is False:
        return str(BACKUP_OFF_UNREAD) % {'name': entry.subject_name, 'room': room}

    return _unknown_event(entry)


@register.filter
def outage_overload_note(outage: OutageRecord) -> str:
    overload = outage.overload
    if overload is None:
        return ''

    phase = PHASE_LABELS.get(overload.phase)
    if phase is None:
        return str(ANY_OVERLOAD_BEFORE_CUT)

    amps = floatformat(overload.amps, 1)
    if not amps:
        return str(UNMEASURED_OVERLOAD_BEFORE_CUT) % {'phase': phase}

    return str(OVERLOAD_BEFORE_CUT) % {'phase': phase, 'amps': amps}


@register.filter
def estimate_parts_text(current: PhaseCurrent) -> str:
    return ESTIMATE_PARTS_SEPARATOR.join(_estimate_part_text(part) for part in current.estimate_parts)


@register.filter
def load_phase(load: LoadOverview) -> str:
    template = LOAD_PHASES.get(load.phase_origin)
    phase = PHASE_LABELS.get(load.phase)
    if template is None or phase is None:
        return str(UNKNOWN_LOAD_PHASE)

    return str(template) % {'phase': phase}


@register.filter
def load_amps(load: LoadOverview) -> str:
    return str(LOAD_AMPS[load.amps_origin]) % {'amps': floatformat(load.amps, 1)}


@register.filter
def load_name(entry: TimelineEntry) -> str:
    if entry.subject_name != entry.subject:
        return entry.subject_name

    name = entry.payload.get('name')
    return name if isinstance(name, str) and name else entry.subject_name


@register.filter
def phase_event_text(entry: TimelineEntry) -> str:
    phase = PHASE_LABELS.get(str(entry.payload.get('phase')))
    if phase is None:
        return _unknown_event(entry)

    if entry.kind == 'ev_charging_stopped':
        return str(EV_CHARGING_STOPPED) % {'phase': phase}

    template = PHASE_EVENTS.get(entry.kind)
    if _estimated(entry):
        template = ESTIMATED_PHASE_EVENTS.get(entry.kind, template)
    amps = floatformat(entry.payload.get('amps'), 1)
    if template is None or not amps:
        return _unknown_event(entry)

    return str(template) % {'phase': phase, 'amps': amps}


@register.filter
def load_shed_text(entry: TimelineEntry) -> str:
    kind = _load_kind(entry)
    template = None if kind is None else LOAD_SHEDS.get((entry.kind, kind))
    if template is None:
        return _unknown_event(entry)

    if kind in SWITCHED_BACK_ON and entry.kind == 'load_restored' and entry.payload.get('switched_on') is True:
        template = SWITCHED_BACK_ON[kind]
    return str(template) % {'name': load_name(entry)}


@register.filter
def load_shed_note(entry: TimelineEntry) -> str:
    cause = entry.payload.get('cause')
    if cause == 'released':
        return str(RESTORED_ON_RELEASE)

    if cause == 'calm':
        return _calm_note(entry)

    if cause == 'limp':
        return str(RESTORED_WHILE_METER_LOST)

    reason = entry.payload.get('reason')
    if reason == 'meter_lost':
        return str(SHED_ON_METER_LOST)

    phase = PHASE_LABELS.get(str(entry.payload.get('phase')))
    if reason != 'overload' or phase is None:
        return ''

    amps = floatformat(entry.payload.get('amps'), 1)
    estimated = _estimated(entry)
    if not amps:
        return str(ESTIMATED_SHED_UNMEASURED_OVERLOAD if estimated else SHED_UNMEASURED_OVERLOAD) % {'phase': phase}

    return str(ESTIMATED_PHASE_OVERLOAD if estimated else PHASE_OVERLOAD) % {'phase': phase, 'amps': amps}


@register.filter
def load_command_text(entry: TimelineEntry) -> str:
    kind = _load_kind(entry)
    template = None if kind is None else LOAD_COMMANDS_FAILED.get((str(entry.payload.get('command')), kind))
    return str(template or ANY_LOAD_COMMAND_FAILED) % {'name': load_name(entry)}


@register.filter
def start_wait_text(entry: TimelineEntry) -> str:
    return str(START_WOULD_WAIT) % {'name': load_name(entry)}


@register.filter
def start_wait_note(entry: TimelineEntry) -> str:
    reason = str(entry.payload.get('reason'))
    template = (ESTIMATED_START_WAITS_FOR_ROOM if _estimated(entry) else START_WAITS_FOR_ROOM).get(reason)
    if template is None:
        return str(START_REFUSALS.get(reason, ''))

    phase = PHASE_LABELS.get(str(entry.payload.get('phase')))
    amps = floatformat(entry.payload.get('amps'), 1)
    if phase is None or not amps:
        return ''

    return str(template) % {'phase': phase, 'amps': amps}


@register.filter
def load_phase_learned_text(entry: TimelineEntry) -> str:
    phase = PHASE_LABELS.get(str(entry.payload.get('phase')))
    amps = floatformat(entry.payload.get('amps'), 1)
    if phase is None or not amps:
        return _unknown_event(entry)

    return str(LOAD_PHASE_LEARNED) % {'name': load_name(entry), 'phase': phase, 'amps': amps}


@register.filter
def weekday_name(weekday: int) -> str:
    return str(WEEKDAYS[weekday])


@register.filter
def weekday_short(weekday: int) -> str:
    return str(WEEKDAYS_ABBR[weekday])


@register.filter
def day_minute(minutes: int) -> str:
    hours, minute = divmod(minutes, 60)
    return f'{hours:02d}:{minute:02d}'


@register.filter
def period_text(period: DayPeriod) -> str:
    return str(DAY_PERIODS[period.mode]) % {'start': day_minute(period.start), 'end': day_minute(period.end)}


@register.filter
def settings_change_text(entry: TimelineEntry) -> str:
    target = str(entry.payload.get('object'))
    field = str(entry.payload.get('field'))
    old = entry.payload.get('old')
    new = entry.payload.get('new')
    name = _settings_subject_name(entry, target)
    if target == 'schedule':
        template = SCHEDULE_CHANGES.get(field)
        if template is None:
            return str(SETTINGS_CHANGED) % {'name': name}

        return str(template) % {'name': name, 'old': _windows_text(old), 'new': _windows_text(new)}

    if target == 'zone' and field == 'name':
        return str(ZONE_RENAMED) % {'name': name, 'old': old}

    if target == 'zone' and field == 'heating_enabled' and isinstance(new, bool):
        return str(HEATING_SWITCHED[new]) % {'name': name}

    if target == ROOM and field == 'guest_room' and isinstance(new, bool):
        return str(GUEST_ROOM_SWITCHED[new]) % {'name': name}

    known = SETTINGS_FIELDS.get((target, field))
    if known is None:
        return str(SETTINGS_CHANGED) % {'name': name}

    label, value_text = known
    return str(SETTING_CHANGED) % {'name': name, 'field': label, 'old': value_text(old), 'new': value_text(new)}


def _settings_subject_name(entry: TimelineEntry, target: str) -> str:
    subject = SETTINGS_SUBJECTS.get(target)
    if subject is not None:
        return str(subject)

    if target != ROOM:
        return load_name(entry)

    name = entry.payload.get('name')
    return name if isinstance(name, str) and name else entry.subject


def _moment_text(at: datetime) -> str:
    day = day_word(at)
    time = timezone.localtime(at).strftime('%H:%M')
    return f'{time} {day}' if day else time


def _hands_off_text(zone: ZoneOverview, until: datetime) -> str:
    if zone.power is False:
        return str(HANDS_OFF_POWER_OFF) % {'time': _moment_text(until)}

    return str(HANDS_OFF_SETPOINT) % {'setpoint': floatformat(zone.setpoint, -1), 'time': _moment_text(until)}


def _manual_changes(payload: dict[str, object], mode_words: Mapping[str, Promise] | None = None) -> list[str]:
    changes: list[str] = []
    power = _change_pair(payload.get('power'))
    if power is not None and isinstance(power[1], bool):
        changes.append(str(MANUAL_POWER[power[1]]))
    mode = _change_pair(payload.get('mode'))
    if mode is not None:
        old, new = (_mode_word(value, mode_words) for value in mode)
        changes.append(str(MANUAL_MODE) % {'old': old, 'new': new})
    setpoint = _change_pair(payload.get('setpoint'))
    if setpoint is not None and all(floatformat(value, -1) for value in setpoint):
        changes.append(MANUAL_SETPOINT % {'old': _degrees(setpoint[0]), 'new': _degrees(setpoint[1])})
    return changes


def _mode_word(value: object, words: Mapping[str, Promise] | None) -> str:
    word = None if words is None else words.get(str(value))
    return str(value) if word is None else str(word)


def _source_mode_text(source: SourceOverview) -> str:
    if not source.on:
        return str(SOURCE_IS_OFF)

    template = SOURCE_MODE_STATES.get(source.mode or ClimateMode.HEAT, SOURCE_HEATS_UP_TO)
    return str(template) % {'setpoint': floatformat(source.setpoint, -1)}


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _change_pair(value: object) -> tuple[object, object] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None

    return value[0], value[1]


def _journaled_moment(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    try:
        return _moment_text(datetime.fromisoformat(value))
    except ValueError:
        return None


def _windows_text(windows: object) -> str:
    if not isinstance(windows, list) or not windows:
        return str(NO_COMFORT)

    return ', '.join(str(window).replace('-', '–') for window in windows)


def _calm_note(entry: TimelineEntry) -> str:
    phase = PHASE_LABELS.get(str(entry.payload.get('phase')))
    amps = floatformat(entry.payload.get('amps'), 1)
    if phase is None or not amps:
        return str(RESTORED_ON_CALM_PHASES)

    template = ESTIMATED_RESTORED_ON_CALM if _estimated(entry) else RESTORED_ON_CALM
    return str(template) % {'phase': phase, 'amps': amps}


def _estimated(entry: TimelineEntry) -> bool:
    return entry.payload.get('estimated') is True


def _estimate_part_text(part: EstimatePartOverview) -> str:
    name = str(ESTIMATE_BASE_NAME) if part.name == ESTIMATE_BASE else part.name
    return str(ESTIMATE_PART) % {'name': name, 'amps': floatformat(part.amps, 1)}


def _load_kind(entry: TimelineEntry) -> LoadKind | None:
    kind = entry.payload.get('kind')
    if kind in (LoadKind.PLUG, LoadKind.ZONE, LoadKind.SOURCE):
        return LoadKind(str(kind))

    return None if entry.device_kind is None else DEVICE_LOAD_KINDS.get(entry.device_kind)


def _air_state_kept(entry: TimelineEntry) -> bool:
    state = entry.payload.get('state')
    return state is not None and state == entry.payload.get('previous_state')


def _air_control_thermostat_text(entry: TimelineEntry) -> str:
    if entry.payload.get('power') is False:
        return str(AIR_CONTROL_OFF) % {'name': entry.subject_name}

    setpoint = floatformat(entry.payload.get('setpoint'), -1)
    if not setpoint:
        return _unknown_event(entry)

    return str(AIR_CONTROL_SETPOINT) % {'name': entry.subject_name, 'setpoint': setpoint}


def _zone_name(entry: TimelineEntry) -> str:
    name = entry.payload.get('name')
    return name if isinstance(name, str) and name else entry.subject_name


def _highest_rate(rates: object) -> Decimal | None:
    if not isinstance(rates, dict) or not rates:
        return None

    try:
        return max(Decimal(str(rate)) for rate in rates.values())
    except InvalidOperation:
        return None


def _occupancy(entry: TimelineEntry) -> Occupancy | None:
    value = entry.payload.get('occupancy')
    return Occupancy(value) if isinstance(value, str) and value in Occupancy else None


def _presence_source_text(source: PresenceOverview, window: float) -> str:
    if not source.available:
        return str(PRESENCE_NO_CONNECTION) % {'name': source.name}

    if source.seen_age_seconds is None or source.seen_age_seconds > window:
        return str(NOBODY_SEEN) % {'name': source.name}

    return str(SOMEONE_MINUTES_AGO) % {'name': source.name, 'minutes': source.seen_age_seconds // 60}


def _unknown_event(entry: TimelineEntry) -> str:
    return str(UNKNOWN_EVENT) % {'name': entry.subject_name}
