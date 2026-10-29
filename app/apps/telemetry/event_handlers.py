from dataclasses import asdict

from apps.climate.domain.events import ZoneBoosted, ZoneCommandFailed, ZoneCommandIssued
from apps.core.domain.events import DomainEvent, domain_event_handler
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.telemetry.repositories.journal import JournalRepository


def _payload(event: DomainEvent) -> dict[str, object]:
    payload = asdict(event)
    payload.pop('occurred_at')
    return payload


@domain_event_handler(DeviceAvailabilityChanged)
def journal_device_availability(event: DeviceAvailabilityChanged) -> None:
    JournalRepository.add(
        kind='device_availability_changed',
        subject=event.slug,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(ZoneCommandIssued)
def journal_zone_command(event: ZoneCommandIssued) -> None:
    JournalRepository.add(
        kind='zone_command_issued',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(ZoneCommandFailed)
def journal_zone_command_failure(event: ZoneCommandFailed) -> None:
    JournalRepository.add(
        kind='zone_command_failed',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(ZoneBoosted)
def journal_zone_boost(event: ZoneBoosted) -> None:
    JournalRepository.add(
        kind='zone_boosted',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )
