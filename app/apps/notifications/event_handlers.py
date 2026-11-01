from apps.core.domain.events import domain_event_handler
from apps.notifications.domain.alerts import AlertKey, AlertTransition
from apps.notifications.domain.events import AlertCleared, AlertEvent, AlertRaised, TeploResumed
from apps.notifications.services.messages import AlertContent, OutageContent
from apps.notifications.services.outbox import alert_outbox


@domain_event_handler(AlertRaised)
def queue_alert_raised(event: AlertRaised) -> None:
    _queue_alert(event, AlertTransition.RAISED)


@domain_event_handler(AlertCleared)
def queue_alert_cleared(event: AlertCleared) -> None:
    _queue_alert(event, AlertTransition.CLEARED)


@domain_event_handler(TeploResumed)
def queue_teplo_resumed(event: TeploResumed) -> None:
    alert_outbox.put(OutageContent(outage=event.outage))


def _queue_alert(event: AlertEvent, transition: AlertTransition) -> None:
    alert_outbox.put(
        AlertContent(
            kind=event.kind,
            transition=transition,
            name=event.name,
            device_kind=event.device_kind,
            since=event.since,
        ),
        alert=AlertKey(kind=event.kind, subject=event.subject),
        transition=transition,
    )
