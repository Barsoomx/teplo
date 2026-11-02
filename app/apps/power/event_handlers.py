from apps.core.domain.events import domain_event_handler
from apps.notifications.domain.events import TeploResumed
from apps.power.domain.outage import PowerCut
from apps.power.usecases.record_outages import RecordOutagesInputDTO, RecordOutagesUseCase


@domain_event_handler(TeploResumed)
def record_power_cut(event: TeploResumed) -> None:
    if not event.outage.power_cut:
        return

    RecordOutagesUseCase.init().execute(
        RecordOutagesInputDTO(
            power_cut=PowerCut(
                began_at=event.outage.began_at,
                ended_at=event.outage.ended_at,
                noticed_at=event.occurred_at,
            ),
        ),
    )
