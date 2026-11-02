from dataclasses import dataclass
from decimal import Decimal

from apps.power.domain.loads import LoadKind, LoadPhase, LoadRecord

PLUG_DRAWING_AMPS = Decimal('0.5')


@dataclass(frozen=True, kw_only=True)
class LoadReading:
    read_at: float
    drawing: bool
    closed: bool | None
    current: Decimal | None


@dataclass(frozen=True, kw_only=True)
class LoadStatus:
    slug: str
    kind: LoadKind
    priority: int
    phase: LoadPhase
    amps: Decimal
    available: bool
    drawing: bool
    shed: bool

    @classmethod
    def of(
        cls,
        record: LoadRecord,
        reading: LoadReading | None,
        *,
        shed: bool,
        default_amps: Decimal,
    ) -> 'LoadStatus':
        return cls(
            slug=record.slug,
            kind=record.kind,
            priority=record.priority,
            phase=record.effective_phase,
            amps=record.effective_amps(default_amps),
            available=reading is not None,
            drawing=reading is not None and reading.drawing,
            shed=shed,
        )


def plug_reading(*, on: bool, current: Decimal, read_at: float) -> LoadReading:
    return LoadReading(read_at=read_at, drawing=on and current > PLUG_DRAWING_AMPS, closed=on, current=current)


def thermostat_reading(
    *,
    power: bool,
    heating: bool | None,
    setpoint: Decimal,
    floor_temperature: Decimal,
    read_at: float,
) -> LoadReading:
    if heating is None:
        return LoadReading(read_at=read_at, drawing=power and setpoint > floor_temperature, closed=None, current=None)

    return LoadReading(read_at=read_at, drawing=power and heating, closed=power and heating, current=None)
