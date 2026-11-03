from dataclasses import asdict

from django import forms
from django.http import QueryDict

from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone_config import (
    NAME_MAX_LENGTH,
    PREHEAT_LEAD_MAX_MINUTES,
    SETPOINT_MAX,
    SETPOINT_MIN,
    ZoneConfigRecord,
)

AIR_FIELDS = ('comfort_air_target', 'night_air_target')


def setpoint_field() -> forms.DecimalField:
    return forms.DecimalField(min_value=SETPOINT_MIN, max_value=SETPOINT_MAX, max_digits=3, decimal_places=1)


class ZoneSettingsForm(forms.Form):
    name = forms.CharField(max_length=NAME_MAX_LENGTH)
    heating_enabled = forms.BooleanField(required=False)
    comfort_floor_setpoint = setpoint_field()
    night_floor_setpoint = setpoint_field()
    boost_floor_setpoint = setpoint_field()
    idle_floor_setpoint = setpoint_field()
    floor_limit_setpoint = setpoint_field()
    comfort_air_target = setpoint_field()
    night_air_target = setpoint_field()
    preheat_lead_minutes = forms.IntegerField(min_value=0, max_value=PREHEAT_LEAD_MAX_MINUTES)

    @classmethod
    def for_zone(cls, zone: ZoneConfigRecord, data: QueryDict | None = None) -> 'ZoneSettingsForm':
        form = cls(data, initial=asdict(zone.config), auto_id=f'zone-{zone.slug}-%s')
        if zone.kind is not ZoneKind.AIR or zone.room is not None:
            for field in AIR_FIELDS:
                del form.fields[field]
        return form
