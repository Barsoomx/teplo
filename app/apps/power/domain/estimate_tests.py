from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.power.domain.estimate import EstimateBasis, EstimatePart, PhaseEstimate, estimate_phase
from apps.power.domain.load_status import LoadReading, plug_reading, source_reading, thermostat_reading
from apps.power.domain.loads import LearnedPhase, LoadKind, LoadPhase, LoadRecord, ShedReason
from apps.power.domain.shedding import Shed

AT = 1000.0
NOW = datetime(2026, 11, 4, 18, 40, tzinfo=UTC)
BASE_AMPS = Decimal('2.0')
DEFAULT_AMPS = Decimal('6.0')
BASE = EstimatePart(load=None, amps=BASE_AMPS, basis=EstimateBasis.BASE)
BOILER = LoadRecord(
    slug='boiler-2f',
    name='Бойлер 2 этаж',
    kind=LoadKind.PLUG,
    zone_slug=None,
    device_slug='plug-boiler-2f',
    priority=2,
    phase=LoadPhase.L2,
    nominal_amps=Decimal('6.0'),
    learned=None,
    live=True,
    shed=None,
)
BATHROOM = LoadRecord(
    slug='bathroom-1f',
    name='Санузел 1 этаж',
    kind=LoadKind.ZONE,
    zone_slug='bathroom-1f',
    device_slug='thermostat-bathroom-1f',
    priority=3,
    phase=LoadPhase.L2,
    nominal_amps=None,
    learned=None,
    live=True,
    shed=None,
)
CONVECTOR = replace(
    BATHROOM,
    slug='convector-office',
    name='Конвектор: кабинет',
    kind=LoadKind.SOURCE,
    zone_slug=None,
    device_slug='heater-office',
    priority=8,
    source_slug='convector-office',
    rated_amps=Decimal('8.7'),
)
CONVECTOR_SHED = Shed(
    load='convector-office',
    phase=LoadPhase.L2,
    amps=Decimal('8.7'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    device='heater-office',
    at=AT - 5,
    occurred_at=NOW,
    settled_at=AT,
)
BOILER_ON = plug_reading(on=True, current=Decimal('6.42'), read_at=AT - 20)
BOILER_OFF = plug_reading(on=False, current=Decimal(0), read_at=AT - 20)
BATHROOM_HEATING = thermostat_reading(
    power=True,
    heating=True,
    setpoint=Decimal('18.0'),
    floor_temperature=Decimal('16.5'),
    read_at=AT - 10,
)
BATHROOM_IDLE = replace(BATHROOM_HEATING, drawing=False, closed=False)
BATHROOM_STARTED = EstimatePart(load='bathroom-1f', amps=DEFAULT_AMPS, basis=EstimateBasis.STARTED)
BOILER_SHED = Shed(
    load='boiler-2f',
    phase=LoadPhase.L2,
    amps=Decimal('6.0'),
    live=True,
    reason=ShedReason.OVERLOAD,
    was_on=True,
    device='plug-boiler-2f',
    at=AT - 5,
    occurred_at=NOW,
    settled_at=AT,
)


def _estimate(
    records: Sequence[LoadRecord],
    readings: Mapping[str, LoadReading | None],
    *,
    live_sheds: Mapping[str, Shed],
    live_starts: Mapping[str, float],
    base_amps: Decimal = BASE_AMPS,
) -> PhaseEstimate:
    return estimate_phase(
        LoadPhase.L2,
        base_amps=base_amps,
        records=records,
        readings=readings,
        live_sheds=live_sheds,
        live_starts=live_starts,
        default_amps=DEFAULT_AMPS,
    )


def test_estimate_of_a_phase_without_loads_is_its_base_current() -> None:
    assert _estimate([], {}, live_sheds={}, live_starts={}) == PhaseEstimate(
        phase=LoadPhase.L2,
        amps=Decimal('2.00'),
        parts=(BASE,),
    )


@pytest.mark.parametrize(
    ('reading', 'expected'),
    [
        (
            BOILER_ON,
            PhaseEstimate(
                phase=LoadPhase.L2,
                amps=Decimal('8.42'),
                parts=(BASE, EstimatePart(load='boiler-2f', amps=Decimal('6.42'), basis=EstimateBasis.MEASURED)),
            ),
        ),
        (BOILER_OFF, PhaseEstimate(phase=LoadPhase.L2, amps=Decimal('2.00'), parts=(BASE,))),
    ],
    ids=['plug_on', 'plug_off'],
)
def test_plug_on_the_phase_counts_the_current_it_meters_while_it_is_on(
    reading: LoadReading,
    expected: PhaseEstimate,
) -> None:
    assert _estimate([BOILER], {'boiler-2f': reading}, live_sheds={}, live_starts={}) == expected


@pytest.mark.parametrize(
    ('record', 'amps'),
    [
        (replace(BATHROOM, nominal_amps=Decimal('5.5')), Decimal('5.5')),
        (replace(BATHROOM, learned=LearnedPhase(phase=LoadPhase.L2, amps=Decimal('7.1'), samples=3)), Decimal('7.1')),
        (BATHROOM, Decimal('6.0')),
    ],
    ids=['nominal', 'learned', 'default'],
)
def test_heating_floor_counts_its_effective_current(record: LoadRecord, amps: Decimal) -> None:
    estimate = _estimate([record], {'bathroom-1f': BATHROOM_HEATING}, live_sheds={}, live_starts={})

    assert estimate.parts == (BASE, EstimatePart(load='bathroom-1f', amps=amps, basis=EstimateBasis.DRAWING))


def test_idle_floor_counts_nothing() -> None:
    estimate = _estimate([BATHROOM], {'bathroom-1f': BATHROOM_IDLE}, live_sheds={}, live_starts={})

    assert (estimate.amps, estimate.parts) == (Decimal('2.00'), (BASE,))


def test_load_without_a_usable_reading_counts_its_full_current() -> None:
    assert _estimate([BOILER, BATHROOM], {'boiler-2f': None}, live_sheds={}, live_starts={}) == PhaseEstimate(
        phase=LoadPhase.L2,
        amps=Decimal('14.00'),
        parts=(
            BASE,
            EstimatePart(load='boiler-2f', amps=Decimal('6.0'), basis=EstimateBasis.UNREAD),
            EstimatePart(load='bathroom-1f', amps=Decimal('6.0'), basis=EstimateBasis.UNREAD),
        ),
    )


@pytest.mark.parametrize(
    ('reading', 'parts'),
    [
        (BOILER_ON, (BASE,)),
        (None, (BASE,)),
        (replace(BOILER_OFF, read_at=AT - 4), (BASE,)),
        (
            replace(BOILER_ON, read_at=AT - 4),
            (BASE, EstimatePart(load='boiler-2f', amps=Decimal('6.42'), basis=EstimateBasis.MEASURED)),
        ),
    ],
    ids=['read_before_the_shed', 'unread', 'read_off_after_the_shed', 'switched_back_on_after_the_shed'],
)
def test_load_shed_live_counts_nothing_until_a_reading_taken_after_the_shed(
    reading: LoadReading | None,
    parts: tuple[EstimatePart, ...],
) -> None:
    estimate = _estimate([BOILER], {'boiler-2f': reading}, live_sheds={'boiler-2f': BOILER_SHED}, live_starts={})

    assert estimate.parts == parts


@pytest.mark.parametrize(
    ('reading', 'started_at', 'parts'),
    [
        (BATHROOM_IDLE, AT - 5, (BASE, BATHROOM_STARTED)),
        (None, AT - 5, (BASE, BATHROOM_STARTED)),
        (BATHROOM_IDLE, AT - 15, (BASE,)),
    ],
    ids=['read_before_the_permit', 'unread_since_the_permit', 'read_idle_after_the_permit'],
)
def test_load_granted_a_live_start_counts_its_effective_current_until_a_poll_reads_it(
    reading: LoadReading | None,
    started_at: float,
    parts: tuple[EstimatePart, ...],
) -> None:
    estimate = _estimate(
        [BATHROOM],
        {'bathroom-1f': reading},
        live_sheds={},
        live_starts={'bathroom-1f': started_at},
    )

    assert estimate.parts == parts


def test_loads_of_unknown_and_other_phases_are_left_out() -> None:
    records = [replace(BOILER, phase=LoadPhase.UNKNOWN), replace(BATHROOM, phase=LoadPhase.L3)]

    estimate = _estimate(records, {'boiler-2f': BOILER_ON, 'bathroom-1f': None}, live_sheds={}, live_starts={})

    assert estimate == PhaseEstimate(phase=LoadPhase.L2, amps=Decimal('2.00'), parts=(BASE,))


def test_parts_of_no_current_are_left_out_and_the_sum_is_rounded_to_hundredths() -> None:
    estimate = _estimate(
        [BOILER, BATHROOM],
        {'boiler-2f': replace(BOILER_ON, current=Decimal('6.015')), 'bathroom-1f': BATHROOM_IDLE},
        live_sheds={},
        live_starts={},
        base_amps=Decimal('0.0'),
    )

    assert estimate == PhaseEstimate(
        phase=LoadPhase.L2,
        amps=Decimal('6.02'),
        parts=(EstimatePart(load='boiler-2f', amps=Decimal('6.015'), basis=EstimateBasis.MEASURED),),
    )


@pytest.mark.parametrize(
    ('reading', 'live_sheds', 'parts'),
    [
        (
            source_reading(on=True, heating=True, read_at=AT - 10),
            {},
            (BASE, EstimatePart(load='convector-office', amps=Decimal('8.7'), basis=EstimateBasis.DRAWING)),
        ),
        (source_reading(on=True, heating=False, read_at=AT - 10), {}, (BASE,)),
        (source_reading(on=True, heating=True, read_at=AT - 10), {'convector-office': CONVECTOR_SHED}, (BASE,)),
        (
            source_reading(on=True, heating=True, read_at=AT - 4),
            {'convector-office': CONVECTOR_SHED},
            (BASE, EstimatePart(load='convector-office', amps=Decimal('8.7'), basis=EstimateBasis.DRAWING)),
        ),
    ],
    ids=['heating', 'at_its_setpoint', 'shed_live_and_unread_since', 'switched_back_on_after_the_shed'],
)
def test_heat_source_counts_its_rated_current_while_it_heats_and_nothing_after_a_live_shed_until_read_again(
    reading: LoadReading,
    live_sheds: Mapping[str, Shed],
    parts: tuple[EstimatePart, ...],
) -> None:
    estimate = _estimate([CONVECTOR], {'convector-office': reading}, live_sheds=live_sheds, live_starts={})

    assert estimate.parts == parts
