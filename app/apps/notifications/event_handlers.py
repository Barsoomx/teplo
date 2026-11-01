from django.conf import settings
from django.utils import translation

from apps.core.domain.events import domain_event_handler
from apps.notifications.domain.alerts import AlertKey, AlertTransition
from apps.notifications.domain.events import AlertCleared, AlertEvent, AlertRaised, TeploResumed
from apps.notifications.services.messages import alert_text, outage_text
from apps.notifications.services.outbox import alert_outbox


@domain_event_handler(AlertRaised)
def queue_alert_raised(event: AlertRaised) -> None:
    _queue_alert(event, AlertTransition.RAISED)


@domain_event_handler(AlertCleared)
def queue_alert_cleared(event: AlertCleared) -> None:
    _queue_alert(event, AlertTransition.CLEARED)


@domain_event_handler(TeploResumed)
def queue_teplo_resumed(event: TeploResumed) -> None:
    with translation.override(settings.TEPLO_ALERT_LANGUAGE):
        alert_outbox.put(outage_text(event.outage))


def _queue_alert(event: AlertEvent, transition: AlertTransition) -> None:
    with translation.override(settings.TEPLO_ALERT_LANGUAGE):
        text = alert_text(
            kind=event.kind,
            transition=transition,
            name=event.name,
            device_kind=event.device_kind,
            since=event.since,
            at=event.occurred_at,
        )
    alert_outbox.put(text, alert=AlertKey(kind=event.kind, subject=event.subject), transition=transition)
