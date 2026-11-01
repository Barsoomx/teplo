from collections.abc import Mapping
from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext, gettext_lazy

from apps.devices.domain.kinds import DeviceKind
from apps.notifications.domain.alerts import AlertKind, AlertTransition
from apps.notifications.domain.outage import Outage

DEVICE_LOST = {
    DeviceKind.THERMOSTAT: gettext_lazy('%(name)s: no connection to the thermostat since %(since)s'),
    DeviceKind.PLUG: gettext_lazy('%(name)s: no connection to the plug since %(since)s'),
}
ANY_DEVICE_LOST = gettext_lazy('%(name)s: no connection since %(since)s')
UNDATED_DEVICE_LOST = {
    DeviceKind.THERMOSTAT: gettext_lazy('%(name)s: no connection to the thermostat'),
    DeviceKind.PLUG: gettext_lazy('%(name)s: no connection to the plug'),
}
ANY_UNDATED_DEVICE_LOST = gettext_lazy('%(name)s: no connection')
DEVICE_FOUND = {
    DeviceKind.THERMOSTAT: gettext_lazy('%(name)s: the thermostat is back online'),
    DeviceKind.PLUG: gettext_lazy('%(name)s: the plug is back online'),
}
ANY_DEVICE_FOUND = gettext_lazy('%(name)s: back online')
ZONE_OFF = gettext_lazy('%(name)s: the thermostat has been off since %(since)s although the schedule wants heat')
UNDATED_ZONE_OFF = gettext_lazy('%(name)s: the thermostat is off although the schedule wants heat')
ZONE_FOLLOWS_SCHEDULE = gettext_lazy('%(name)s: the thermostat follows the schedule again')
TEPLO_RESUMED = gettext_lazy('teplo is running again. Outage %(began)s–%(ended)s (%(duration)s)')
DELIVERY_CHECK = gettext_lazy('teplo: test message. Alerts will arrive in this chat')


def alert_text(
    kind: AlertKind,
    transition: AlertTransition,
    name: str,
    device_kind: DeviceKind,
    since: datetime | None,
    at: datetime,
) -> str:
    template = _template(kind, transition, device_kind, dated=since is not None)
    return template % {'name': name, 'since': '' if since is None else _moment(since, at)}


def alert_text_from_payload(transition: AlertTransition, payload: Mapping[str, object], at: datetime) -> str | None:
    try:
        return alert_text(
            kind=AlertKind(str(payload['kind'])),
            transition=transition,
            name=str(payload['name']),
            device_kind=DeviceKind(str(payload['device_kind'])),
            since=_since(payload['since']),
            at=at,
        )
    except (KeyError, ValueError):
        return None


def outage_text(outage: Outage) -> str:
    began = outage.began_at.replace(second=0, microsecond=0)
    ended = outage.ended_at.replace(second=0, microsecond=0)
    return str(TEPLO_RESUMED) % {
        'began': _moment(began, ended),
        'ended': date_format(timezone.localtime(ended), 'H:i'),
        'duration': _duration(ended - began),
    }


def delivery_check_text() -> str:
    return str(DELIVERY_CHECK)


def _template(kind: AlertKind, transition: AlertTransition, device_kind: DeviceKind, *, dated: bool) -> str:
    if transition is AlertTransition.CLEARED:
        if kind is AlertKind.ZONE_OFF:
            return str(ZONE_FOLLOWS_SCHEDULE)

        return str(DEVICE_FOUND.get(device_kind, ANY_DEVICE_FOUND))

    if kind is AlertKind.ZONE_OFF:
        return str(ZONE_OFF if dated else UNDATED_ZONE_OFF)

    if dated:
        return str(DEVICE_LOST.get(device_kind, ANY_DEVICE_LOST))

    return str(UNDATED_DEVICE_LOST.get(device_kind, ANY_UNDATED_DEVICE_LOST))


def _since(value: object) -> datetime | None:
    if value is None:
        return None

    return datetime.fromisoformat(str(value))


def _moment(moment: datetime, reference: datetime) -> str:
    local = timezone.localtime(moment)
    if local.date() == timezone.localtime(reference).date():
        return date_format(local, 'H:i')

    return date_format(local, 'j E H:i')


def _duration(duration: timedelta) -> str:
    hours, minutes = divmod(int(duration.total_seconds()) // 60, 60)
    if not hours:
        return gettext('%(minutes)s min') % {'minutes': minutes}

    if not minutes:
        return gettext('%(hours)s h') % {'hours': hours}

    return gettext('%(hours)s h %(minutes)s min') % {'hours': hours, 'minutes': minutes}
