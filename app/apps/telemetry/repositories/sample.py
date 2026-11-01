from collections.abc import Sequence
from datetime import datetime

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample


class SampleRepository:
    @staticmethod
    def add_many(samples: Sequence[SampleValue]) -> int:
        created = Sample.objects.bulk_create(
            [
                Sample(metric=sample.metric, subject=sample.subject, value=sample.value, recorded_at=sample.recorded_at)
                for sample in samples
            ],
        )
        return len(created)

    @staticmethod
    def last_recorded_at() -> datetime | None:
        return Sample.objects.order_by('-pk').values_list('recorded_at', flat=True).first()
