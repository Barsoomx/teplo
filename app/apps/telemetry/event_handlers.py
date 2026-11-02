from dataclasses import asdict

from apps.climate.domain.events import (
    AirControlDryRun,
    OptimalStartDryRun,
    SecondFloorGuardDryRun,
    ZoneBoosted,
    ZoneCommandFailed,
    ZoneCommandIssued,
)
from apps.core.domain.events import DomainEvent, domain_event_handler
from apps.devices.domain.events import DeviceAvailabilityChanged
from apps.notifications.domain.events import AlertCleared, AlertRaised
from apps.power.domain.events import (
    EvChargingStarted,
    EvChargingStopped,
    LoadCommandFailed,
    LoadPhaseLearned,
    LoadRestored,
    LoadRestoredDryRun,
    LoadShed,
    LoadShedDryRun,
    LoadShedOverridden,
    PhaseCalmed,
    PhaseOverloaded,
)
from apps.telemetry.repositories.journal import JournalRepository


def _payload(event: DomainEvent) -> dict[str, object]:
    payload = asdict(event)
    payload.pop('occurred_at')
    return payload


def _journal(kind: str, subject: str, event: DomainEvent) -> None:
    JournalRepository.add(kind=kind, subject=subject, payload=_payload(event), created_at=event.occurred_at)


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


@domain_event_handler(AirControlDryRun)
def journal_air_control_dry_run(event: AirControlDryRun) -> None:
    JournalRepository.add(
        kind='air_control_dry_run',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(OptimalStartDryRun)
def journal_optimal_start_dry_run(event: OptimalStartDryRun) -> None:
    JournalRepository.add(
        kind='optimal_start_dry_run',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(SecondFloorGuardDryRun)
def journal_second_floor_guard_dry_run(event: SecondFloorGuardDryRun) -> None:
    JournalRepository.add(
        kind='second_floor_guard_dry_run',
        subject=event.zone,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(AlertRaised)
def journal_alert_raised(event: AlertRaised) -> None:
    JournalRepository.add(
        kind='alert_raised',
        subject=event.subject,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(AlertCleared)
def journal_alert_cleared(event: AlertCleared) -> None:
    JournalRepository.add(
        kind='alert_cleared',
        subject=event.subject,
        payload=_payload(event),
        created_at=event.occurred_at,
    )


@domain_event_handler(PhaseOverloaded)
def journal_phase_overload(event: PhaseOverloaded) -> None:
    _journal('phase_overload', event.phase, event)


@domain_event_handler(PhaseCalmed)
def journal_phase_calm(event: PhaseCalmed) -> None:
    _journal('phase_calm', event.phase, event)


@domain_event_handler(LoadShed)
def journal_load_shed(event: LoadShed) -> None:
    _journal('load_shed', event.load, event)


@domain_event_handler(LoadShedDryRun)
def journal_load_shed_dry_run(event: LoadShedDryRun) -> None:
    _journal('load_shed_dry_run', event.load, event)


@domain_event_handler(LoadRestored)
def journal_load_restored(event: LoadRestored) -> None:
    _journal('load_restored', event.load, event)


@domain_event_handler(LoadRestoredDryRun)
def journal_load_restored_dry_run(event: LoadRestoredDryRun) -> None:
    _journal('load_restored_dry_run', event.load, event)


@domain_event_handler(LoadShedOverridden)
def journal_load_shed_overridden(event: LoadShedOverridden) -> None:
    _journal('load_shed_overridden', event.load, event)


@domain_event_handler(LoadCommandFailed)
def journal_load_command_failure(event: LoadCommandFailed) -> None:
    _journal('load_command_failed', event.load, event)


@domain_event_handler(LoadPhaseLearned)
def journal_load_phase_learned(event: LoadPhaseLearned) -> None:
    _journal('load_phase_learned', event.load, event)


@domain_event_handler(EvChargingStarted)
def journal_ev_charging_started(event: EvChargingStarted) -> None:
    _journal('ev_charging_started', event.phase, event)


@domain_event_handler(EvChargingStopped)
def journal_ev_charging_stopped(event: EvChargingStopped) -> None:
    _journal('ev_charging_stopped', event.phase, event)
