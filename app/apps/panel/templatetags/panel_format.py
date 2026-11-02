from datetime import datetime, timedelta

from django import template
from django.template.defaultfilters import floatformat
from django.templatetags.static import static
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext, gettext_lazy

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertTransition
from apps.notifications.services.messages import alert_text_from_payload, duration_text, interval_parts
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.panel.usecases.get_panel_overview import LoadOverview
from apps.power.domain.loads import MEASURED_PHASES, AmpsOrigin, PhaseOrigin
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
OVERLOAD_BEFORE_CUT = gettext_lazy('Phase %(phase)s was overloaded right before the cut: %(amps)s A')
UNMEASURED_OVERLOAD_BEFORE_CUT = gettext_lazy('Phase %(phase)s was overloaded right before the cut')
ANY_OVERLOAD_BEFORE_CUT = gettext_lazy('A phase was overloaded right before the cut')


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

    template = AIR_CONTROL.get(str(entry.payload.get('state')))
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
