from apps.power.domain.loads import LearnedPhase, LiveShed, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.models import Load


class LoadRepository:
    @staticmethod
    def all_records() -> list[LoadRecord]:
        loads = Load.objects.select_related('zone__thermostat', 'device').order_by('priority')
        return [record for record in map(_to_record, loads) if record is not None]

    @staticmethod
    def live_zone_slugs() -> frozenset[str]:
        return frozenset(Load.objects.filter(zone__live_control=True).values_list('zone__slug', flat=True))

    @staticmethod
    def mark_shed(slug: str, shed: LiveShed) -> None:
        Load.objects.filter(slug=slug).update(
            shed_at=shed.at,
            shed_reason=shed.reason,
            shed_phase=shed.phase,
            shed_was_on=shed.was_on,
            shed_device=shed.device,
        )

    @staticmethod
    def clear_shed(slug: str) -> None:
        Load.objects.filter(slug=slug).update(
            shed_at=None,
            shed_reason='',
            shed_phase='',
            shed_was_on=None,
            shed_device='',
        )

    @staticmethod
    def save_learned(slug: str, learned: LearnedPhase) -> None:
        Load.objects.filter(slug=slug).update(
            learned_phase=learned.phase,
            learned_amps=learned.amps,
            learning_samples=learned.samples,
        )


def _to_record(load: Load) -> LoadRecord | None:
    device = load.device if load.zone is None else load.zone.thermostat
    if device is None:
        return None

    return LoadRecord(
        slug=load.slug,
        name=load.name,
        kind=LoadKind(load.kind),
        zone_slug=None if load.zone is None else load.zone.slug,
        device_slug=device.slug,
        priority=load.priority,
        phase=LoadPhase(load.phase),
        nominal_amps=load.nominal_amps,
        learned=_learned(load),
        live=load.live,
        shed=_shed(load),
        last_command_at=None if load.zone is None else load.zone.last_command_at,
    )


def _learned(load: Load) -> LearnedPhase | None:
    if load.learned_phase == LoadPhase.UNKNOWN or load.learned_amps is None:
        return None

    return LearnedPhase(phase=LoadPhase(load.learned_phase), amps=load.learned_amps, samples=load.learning_samples)


def _shed(load: Load) -> LiveShed | None:
    if load.shed_at is None:
        return None

    return LiveShed(
        at=load.shed_at,
        reason=ShedReason(load.shed_reason),
        phase=LoadPhase(load.shed_phase),
        was_on=load.shed_was_on,
        device=load.shed_device,
    )
