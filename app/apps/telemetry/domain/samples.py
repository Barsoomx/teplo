from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class SampleValue:
    metric: str
    subject: str
    value: float
    recorded_at: datetime
