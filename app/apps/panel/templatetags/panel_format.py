from datetime import datetime, timedelta

from django import template
from django.template.defaultfilters import floatformat
from django.templatetags.static import static
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext

register = template.Library()


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
