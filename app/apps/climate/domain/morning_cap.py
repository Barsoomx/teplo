from decimal import Decimal

from apps.climate.domain.learning import HeatUpModel
from apps.climate.domain.optimal_start import heat_up_rate

MORNING_RATE_FLOOR = Decimal('1.5')
MORNING_RATE_FACTOR = Decimal(3)


def morning_rate_cap(model: HeatUpModel, outdoor_bucket: int) -> Decimal:
    rate = heat_up_rate(model, Decimal(outdoor_bucket))
    if rate is None:
        return MORNING_RATE_FLOOR

    return max(MORNING_RATE_FLOOR, MORNING_RATE_FACTOR * rate)
