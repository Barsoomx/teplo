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
from apps.notifications.services.messages import alert_text_from_payload
from apps.panel.usecases.get_journal_timeline import TimelineEntry
from apps.system.domain.clock import ClockStatusCode

register = template.Library()

FAILURE_NOTES = {
    'device_unavailable': gettext_lazy('No connection to the thermostat'),
    'device_protocol_error': gettext_lazy('The thermostat sent an unexpected reply'),
    'driver_not_implemented': gettext_lazy('teplo cannot control this device yet'),
    'device_not_found': gettext_lazy('The device is missing from the settings'),
    'secret_missing': gettext_lazy('The secrets file has no address and key for this thermostat'),
}
PLUG_FAILURE_NOTES = {
    **FAILURE_NOTES,
    'device_unavailable': gettext_lazy('No connection to the plug'),
    'device_protocol_error': gettext_lazy('The plug sent an unexpected reply'),
    'secret_missing': gettext_lazy('The secrets file has no address and key for this plug'),
}
UNKNOWN_FAILURE = gettext_lazy('Unknown error')
CLOCK_NOTES = {
    ClockStatusCode.NTP_STATUS_MISSING: gettext_lazy('the router reports no NTP status'),
    ClockStatusCode.NTP_STATUS_FAILED: gettext_lazy('could not read the NTP status from the router'),
}
ALERT_TRANSITIONS = {'alert_raised': AlertTransition.RAISED, 'alert_cleared': AlertTransition.CLEARED}


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

    notes = PLUG_FAILURE_NOTES if device == DeviceKind.PLUG else FAILURE_NOTES
    return str(notes.get(error_code, UNKNOWN_FAILURE))


@register.simple_tag
def clock_note(code: ClockStatusCode) -> str:
    return str(CLOCK_NOTES.get(code, ''))


@register.filter
def local_time(value: str) -> str:
    return timezone.localtime(datetime.fromisoformat(value)).strftime('%H:%M')


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
        return gettext('%(name)s: unknown event') % {'name': entry.subject_name}

    return text
