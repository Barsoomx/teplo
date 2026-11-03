from dataclasses import asdict

from django import forms
from django.http import QueryDict

from apps.power.domain.load_config import AMPS_MAX, AMPS_MIN, SECONDS_MAX, SECONDS_MIN, LoadSettingsRecord


def amps_field(*, required: bool = True) -> forms.DecimalField:
    return forms.DecimalField(
        required=required,
        min_value=AMPS_MIN,
        max_value=AMPS_MAX,
        max_digits=4,
        decimal_places=1,
    )


def seconds_field() -> forms.IntegerField:
    return forms.IntegerField(min_value=SECONDS_MIN, max_value=SECONDS_MAX)


class LoadSettingsForm(forms.Form):
    start_ceiling_amps = amps_field()
    shed_amps = amps_field()
    shed_hold_seconds = seconds_field()
    restore_amps = amps_field()
    restore_hold_seconds = seconds_field()
    start_interval_seconds = seconds_field()
    meter_lost_after_seconds = seconds_field()
    default_load_amps = amps_field()

    @classmethod
    def for_settings(cls, record: LoadSettingsRecord, data: QueryDict | None = None) -> 'LoadSettingsForm':
        return cls(data, initial=asdict(record.config), auto_id='load-settings-%s')
