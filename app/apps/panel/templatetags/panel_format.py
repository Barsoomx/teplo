from collections.abc import Callable
from datetime import datetime, timedelta

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
from apps.climate.domain.modes import CommandSource, ScheduleMode
from apps.climate.domain.room_climate import ROOM
from apps.climate.domain.room_intent import OVERRIDE_STEP, OVERRIDE_TARGET_MAX, OVERRIDE_TARGET_MIN, RoomIntent
from apps.climate.domain.week_plan import DayPeriod
from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertTransition
from apps.notifications.services.messages import alert_text_from_payload, duration_text, interval_parts
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.panel.usecases.get_panel_overview import LoadOverview, ZoneOverview
from apps.power.domain.loads import MEASURED_PHASES, AmpsOrigin, LoadKind, PhaseOrigin
from apps.power.domain.outage import OutageRecord
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
SENSOR_FAILURE_NOTES = {
    **FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the sensor'),
    'device_protocol_error': gettext_lazy('The sensor sent an unexpected reply'),
    'sensor_reading_failed': gettext_lazy('The sensor could not take a reading'),
    'broker_unavailable': gettext_lazy('teplo cannot connect to the MQTT broker'),
}
PHASE_METER_FAILURE_NOTES = {
    **SENSOR_FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the phase meter'),
    'device_protocol_error': gettext_lazy('The phase meter sent an unexpected reply'),
    'sensor_reading_failed': gettext_lazy('The phase meter could not measure a phase'),
}
DEVICE_FAILURE_NOTES = {
    DeviceKind.PLUG.value: PLUG_FAILURE_NOTES,
    DeviceKind.TEMPERATURE_SENSOR.value: SENSOR_FAILURE_NOTES,
    DeviceKind.PHASE_METER.value: PHASE_METER_FAILURE_NOTES,
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
GUESTS_UNTIL = gettext_lazy('Guests until %(time)s')
GUESTS_OFF = gettext_lazy('Guests mode is off')
ROOM_OVERRIDDEN = gettext_lazy('%(name)s: manual %(target)s° until %(time)s')
ROOM_OVERRIDE_CLEARED = gettext_lazy('%(name)s: back to the schedule')
FROM_TELEGRAM = gettext_lazy('From Telegram')
PHASE_LABELS = {phase.value: phase.value.upper() for phase in MEASURED_PHASES}
LOAD_PHASES = {
    PhaseOrigin.SET: gettext_lazy('Phase %(phase)s, set'),
    PhaseOrigin.LEARNED: gettext_lazy('Phase %(phase)s, learned'),
}
UNKNOWN_LOAD_PHASE = gettext_lazy('Phase unknown')
LOAD_AMPS = {
    AmpsOrigin.NOMINAL: gettext_lazy('%(amps)s A, nominal'),
    AmpsOrigin.LEARNED: gettext_lazy('%(amps)s A, learned'),
    AmpsOrigin.DEFAULT: gettext_lazy('%(amps)s A by default'),
}
PHASE_OVERLOAD = gettext_lazy('Overload on phase %(phase)s: %(amps)s A')
PHASE_EVENTS = {
    'phase_overload': PHASE_OVERLOAD,
    'phase_calm': gettext_lazy('Overload on phase %(phase)s is over: %(amps)s A'),
    'ev_charging_started': gettext_lazy('Phase %(phase)s: an EV seems to be charging, +%(amps)s A'),
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
}
ZONE_SWITCHED_BACK_ON = gettext_lazy('%(name)s: switched the thermostat back on')
SHED_UNMEASURED_OVERLOAD = gettext_lazy('Overload on phase %(phase)s')
SHED_ON_METER_LOST = gettext_lazy('No readings from the phase meter: one floor per phase')
RESTORED_ON_CALM = gettext_lazy('Phase %(phase)s back to normal: %(amps)s A')
RESTORED_ON_CALM_PHASES = gettext_lazy('Phases back to normal')
RESTORED_ON_RELEASE = gettext_lazy('Load management was switched in the settings')
RESTORED_WHILE_METER_LOST = gettext_lazy('No readings from the phase meter: no other floor heats on this phase')
LOAD_COMMANDS_FAILED = {
    ('shed', LoadKind.PLUG): gettext_lazy('%(name)s: could not switch the plug off'),
    ('restore', LoadKind.PLUG): gettext_lazy('%(name)s: could not switch the plug back on'),
    ('shed', LoadKind.ZONE): gettext_lazy('%(name)s: could not switch the thermostat off'),
    ('restore', LoadKind.ZONE): gettext_lazy('%(name)s: could not switch the thermostat back on'),
}
ANY_LOAD_COMMAND_FAILED = gettext_lazy('%(name)s: a load management command failed')
START_WOULD_WAIT = gettext_lazy('%(name)s: would put off switching on')
START_WAITS_FOR_ROOM = {
    'no_headroom': gettext_lazy('Phase %(phase)s would reach %(amps)s A'),
    'phase_would_overload': gettext_lazy(
        'The phase meter is silent and phase %(phase)s would reach %(amps)s A by its last reading',
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
DEVICE_LOAD_KINDS = {DeviceKind.PLUG: LoadKind.PLUG, DeviceKind.THERMOSTAT: LoadKind.ZONE}
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
    if pending is None or pending.kind is not PendingKind.NEXT_WRITE or pending.at is None:
        return ''

    values = {'setpoint': floatformat(zone.setpoint, -1), 'target': floatformat(zone.target_setpoint, -1)}
    if pending.at <= timezone.now():
        return str(NEXT_WRITE_SOON) % values

    return str(NEXT_WRITE_AT) % {**values, 'time': _moment_text(pending.at)}


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
        return str(ROOM_OVERRIDE_CLEARED) % {'name': name}

    target = floatformat(entry.payload.get('target'), 1)
    moment = _journaled_moment(entry.payload.get('until'))
    if not target or moment is None:
        return _unknown_event(entry)

    return str(ROOM_OVERRIDDEN) % {'name': name, 'target': target, 'time': moment}


@register.filter
def command_note(entry: TimelineEntry) -> str:
    return str(FROM_TELEGRAM) if entry.payload.get('source') == CommandSource.TELEGRAM else ''


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

    if kind is LoadKind.ZONE and entry.kind == 'load_restored' and entry.payload.get('switched_on') is True:
        template = ZONE_SWITCHED_BACK_ON
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
    if not amps:
        return str(SHED_UNMEASURED_OVERLOAD) % {'phase': phase}

    return str(PHASE_OVERLOAD) % {'phase': phase, 'amps': amps}


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
    template = START_WAITS_FOR_ROOM.get(reason)
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

    return str(RESTORED_ON_CALM) % {'phase': phase, 'amps': amps}


def _load_kind(entry: TimelineEntry) -> LoadKind | None:
    kind = entry.payload.get('kind')
    if kind in (LoadKind.PLUG, LoadKind.ZONE):
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


def _unknown_event(entry: TimelineEntry) -> str:
    return str(UNKNOWN_EVENT) % {'name': entry.subject_name}
