from dataclasses import asdict

from django import forms
from django.http import QueryDict

from apps.climate.domain.climate_config import (
    MORNINGS_NEEDED_MAX,
    MORNINGS_NEEDED_MIN,
    OFFSET_MAX,
    OFFSET_MIN,
    PREHEAT_LEAD_CAP_MAX_MINUTES,
    ClimateConfigRecord,
)
from apps.climate.domain.learning import EARLY_STOP_OFFSET_MAX, EARLY_STOP_OFFSET_MIN
from apps.climate.domain.zone_config import SETPOINT_MAX, SETPOINT_MIN


def offset_field() -> forms.DecimalField:
    return forms.DecimalField(min_value=OFFSET_MIN, max_value=OFFSET_MAX, max_digits=3, decimal_places=2)


class ClimateSettingsForm(forms.Form):
    air_start_offset = offset_field()
    initial_early_stop_offset = forms.DecimalField(
        min_value=EARLY_STOP_OFFSET_MIN,
        max_value=EARLY_STOP_OFFSET_MAX,
        max_digits=3,
        decimal_places=2,
    )
    learned_mornings_needed = forms.IntegerField(min_value=MORNINGS_NEEDED_MIN, max_value=MORNINGS_NEEDED_MAX)
    preheat_lead_cap_minutes = forms.IntegerField(min_value=0, max_value=PREHEAT_LEAD_CAP_MAX_MINUTES)
    second_floor_guard_threshold = forms.DecimalField(
        min_value=SETPOINT_MIN,
        max_value=SETPOINT_MAX,
        max_digits=3,
        decimal_places=1,
    )
    second_floor_guard_hysteresis = offset_field()

    @classmethod
    def for_settings(cls, record: ClimateConfigRecord, data: QueryDict | None = None) -> 'ClimateSettingsForm':
        return cls(data, initial=asdict(record.config), auto_id='climate-%s')
