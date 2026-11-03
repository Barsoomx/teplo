from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

EMA_ALPHA = Decimal('0.3')
EARLY_STOP_OFFSET_MIN = Decimal('0.00')
EARLY_STOP_OFFSET_MAX = Decimal('1.50')
EARLY_STOP_WATCH = timedelta(hours=2)
OUTDOOR_BUCKET_WIDTH = 5
DEAD_TIME_RISE = Decimal('0.1')
MORNING_MIN_RISE = Decimal('0.2')
OFFSET_STEP = Decimal('0.01')
RATE_STEP = Decimal('0.001')
SECOND = timedelta(seconds=1)
MICROSECOND = timedelta(microseconds=1)
MICROSECONDS_PER_HOUR = Decimal(3_600_000_000)


@dataclass(frozen=True, kw_only=True)
class HeatUpModel:
    dead_time: timedelta | None
    rates: dict[int, Decimal]
    mornings: int


@dataclass(frozen=True, kw_only=True)
class ZoneLearningRecord:
    early_stop_offset: Decimal | None
    heat_up: HeatUpModel

    @classmethod
    def unlearned(cls) -> 'ZoneLearningRecord':
        return cls(early_stop_offset=None, heat_up=HeatUpModel(dead_time=None, rates={}, mornings=0))

    def early_stop_offset_or(self, initial: Decimal) -> Decimal:
        return initial if self.early_stop_offset is None else self.early_stop_offset


@dataclass(frozen=True, kw_only=True)
class AirSample:
    at: datetime
    air: Decimal


@dataclass(frozen=True, kw_only=True)
class Morning:
    comfort_at: datetime
    target: Decimal
    outdoor: Decimal
    samples: tuple[AirSample, ...]


@dataclass(frozen=True, kw_only=True)
class MorningEstimate:
    dead_time: timedelta
    rate: Decimal
    outdoor_bucket: int


def learn_early_stop_offset(offset: Decimal, *, used: Decimal, peak: Decimal, target: Decimal) -> Decimal:
    learned = _ema(offset, used + peak - target)
    clamped = min(max(learned, EARLY_STOP_OFFSET_MIN), EARLY_STOP_OFFSET_MAX)
    return clamped.quantize(OFFSET_STEP, rounding=ROUND_HALF_UP)


def outdoor_bucket(outdoor: Decimal) -> int:
    return int((outdoor / OUTDOOR_BUCKET_WIDTH).to_integral_value(rounding=ROUND_FLOOR)) * OUTDOOR_BUCKET_WIDTH


def estimate_morning(morning: Morning) -> MorningEstimate | None:
    samples = [sample for sample in morning.samples if sample.at <= morning.comfort_at]
    if not samples:
        return None

    start = samples[0]
    risen_at = next(
        (index for index, sample in enumerate(samples) if sample.air - start.air >= DEAD_TIME_RISE),
        None,
    )
    if risen_at is None:
        return None

    risen = samples[risen_at]
    end = next((sample for sample in samples[risen_at:] if sample.air >= morning.target), samples[-1])
    rise = end.air - risen.air
    if rise <= MORNING_MIN_RISE or end.at <= risen.at:
        return None

    return MorningEstimate(
        dead_time=risen.at - start.at,
        rate=(rise / _hours(end.at - risen.at)).quantize(RATE_STEP, rounding=ROUND_HALF_UP),
        outdoor_bucket=outdoor_bucket(morning.outdoor),
    )


def learn_morning(model: HeatUpModel, estimate: MorningEstimate) -> HeatUpModel:
    previous_rate = model.rates.get(estimate.outdoor_bucket)
    rate = (
        estimate.rate
        if previous_rate is None
        else _ema(previous_rate, estimate.rate).quantize(RATE_STEP, rounding=ROUND_HALF_UP)
    )
    return HeatUpModel(
        dead_time=estimate.dead_time if model.dead_time is None else _ema_duration(model.dead_time, estimate.dead_time),
        rates={**model.rates, estimate.outdoor_bucket: rate},
        mornings=model.mornings + 1,
    )


def _ema(previous: Decimal, sample: Decimal) -> Decimal:
    return previous + EMA_ALPHA * (sample - previous)


def _ema_duration(previous: timedelta, sample: timedelta) -> timedelta:
    seconds = _ema(Decimal(previous // SECOND), Decimal(sample // SECOND))
    return timedelta(seconds=int(seconds.to_integral_value(rounding=ROUND_HALF_UP)))


def _hours(duration: timedelta) -> Decimal:
    return Decimal(duration // MICROSECOND) / MICROSECONDS_PER_HOUR
