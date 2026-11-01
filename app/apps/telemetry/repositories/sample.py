from collections.abc import Sequence
from datetime import datetime

from django.db.models import Max

from apps.telemetry.domain.samples import SampleValue
from apps.telemetry.models import Sample

RECENT_SAMPLES = 5000


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
        recent = Sample.objects.order_by('-pk')[:RECENT_SAMPLES]
        return recent.aggregate(last=Max('recorded_at'))['last']
