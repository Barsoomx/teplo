from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from apps.climate.domain.learning import HeatUpModel, outdoor_bucket

SECONDS_PER_HOUR = Decimal(3600)


@dataclass(frozen=True, kw_only=True)
class OptimalStartSettings:
    lead_cap: timedelta
    mornings_needed: int


def heat_up_rate(model: HeatUpModel, outdoor: Decimal) -> Decimal | None:
    learned = {bucket: rate for bucket, rate in model.rates.items() if rate > 0}
    if not learned:
        return None

    bucket = outdoor_bucket(outdoor)
    return learned[min(learned, key=lambda candidate: (abs(candidate - bucket), candidate))]


def optimal_lead(
    model: HeatUpModel,
    *,
    air: Decimal | None,
    outdoor: Decimal | None,
    target: Decimal,
    fixed_lead: timedelta,
    settings: OptimalStartSettings,
) -> timedelta:
    dead_time = timedelta(0) if model.dead_time is None else model.dead_time
    learned = _learned_lead(
        model,
        dead_time,
        air=air,
        outdoor=outdoor,
        target=target,
        mornings_needed=settings.mornings_needed,
    )
    return min(settings.lead_cap, max(dead_time, fixed_lead if learned is None else learned))


def _learned_lead(
    model: HeatUpModel,
    dead_time: timedelta,
    *,
    air: Decimal | None,
    outdoor: Decimal | None,
    target: Decimal,
    mornings_needed: int,
) -> timedelta | None:
    if model.mornings < mornings_needed or air is None or outdoor is None:
        return None

    rate = heat_up_rate(model, outdoor)
    if rate is None:
        return None

    seconds = max(Decimal(0), target - air) / rate * SECONDS_PER_HOUR
    return dead_time + timedelta(seconds=int(seconds.to_integral_value(rounding=ROUND_CEILING)))
