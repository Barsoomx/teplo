from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from apps.power.domain.load_status import LoadReading
from apps.power.domain.loads import LoadPhase, LoadRecord
from apps.power.domain.shedding import Shed

ESTIMATE_STEP = Decimal('0.01')


class EstimateBasis(StrEnum):
    BASE = 'base'
    MEASURED = 'measured'
    DRAWING = 'drawing'
    STARTED = 'started'
    UNREAD = 'unread'


@dataclass(frozen=True, kw_only=True)
class EstimatePart:
    load: str | None
    amps: Decimal
    basis: EstimateBasis


@dataclass(frozen=True, kw_only=True)
class PhaseEstimate:
    phase: LoadPhase
    amps: Decimal
    parts: tuple[EstimatePart, ...]


def estimate_phase(
    phase: LoadPhase,
    *,
    base_amps: Decimal,
    records: Sequence[LoadRecord],
    readings: Mapping[str, LoadReading | None],
    live_sheds: Mapping[str, Shed],
    live_starts: Mapping[str, float],
    default_amps: Decimal,
) -> PhaseEstimate:
    loads = (
        _part(
            record,
            readings.get(record.slug),
            shed=live_sheds.get(record.slug),
            started_at=live_starts.get(record.slug),
            default_amps=default_amps,
        )
        for record in records
        if record.effective_phase is phase
    )
    parts = tuple(
        part
        for part in (EstimatePart(load=None, amps=base_amps, basis=EstimateBasis.BASE), *loads)
        if part is not None and part.amps > 0
    )
    return PhaseEstimate(
        phase=phase,
        amps=sum((part.amps for part in parts), Decimal(0)).quantize(ESTIMATE_STEP, rounding=ROUND_HALF_UP),
        parts=parts,
    )


def _part(
    record: LoadRecord,
    reading: LoadReading | None,
    *,
    shed: Shed | None,
    started_at: float | None,
    default_amps: Decimal,
) -> EstimatePart | None:
    if shed is not None and _unread_since(reading, shed.at):
        return None

    amps = record.effective_amps(default_amps)
    if started_at is not None and _unread_since(reading, started_at):
        return EstimatePart(load=record.slug, amps=amps, basis=EstimateBasis.STARTED)

    if reading is None:
        return EstimatePart(load=record.slug, amps=amps, basis=EstimateBasis.UNREAD)

    if reading.current is not None:
        return EstimatePart(
            load=record.slug,
            amps=reading.current if reading.closed else Decimal(0),
            basis=EstimateBasis.MEASURED,
        )

    return EstimatePart(load=record.slug, amps=amps if reading.drawing else Decimal(0), basis=EstimateBasis.DRAWING)


def _unread_since(reading: LoadReading | None, at: float) -> bool:
    return reading is None or reading.read_at <= at
