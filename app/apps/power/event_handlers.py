from apps.core.domain.events import domain_event_handler
from apps.notifications.domain.events import TeploResumed
from apps.power.usecases.record_outage import RecordOutageInputDTO, RecordOutageUseCase


@domain_event_handler(TeploResumed)
def record_power_cut(event: TeploResumed) -> None:
    if not event.outage.power_cut:
        return

    RecordOutageUseCase.init().execute(
        RecordOutageInputDTO(
            began_at=event.outage.began_at,
            ended_at=event.outage.ended_at,
            now=event.occurred_at,
        ),
    )
