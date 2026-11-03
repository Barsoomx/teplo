from dataclasses import asdict

from django import forms
from django.http import QueryDict

from apps.climate.domain.room_climate import RoomClimateConfigRecord
from apps.panel.forms.climate_settings import offset_field
from apps.panel.forms.zone_settings import setpoint_field

BACKUP_FIELDS = ('backup_below', 'backup_hysteresis')


class RoomSettingsForm(forms.Form):
    comfort_target = setpoint_field()
    setback_target = setpoint_field()
    backup_below = setpoint_field()
    backup_hysteresis = offset_field()
    guest_room = forms.BooleanField(required=False)

    @classmethod
    def for_room(cls, record: RoomClimateConfigRecord, data: QueryDict | None = None) -> 'RoomSettingsForm':
        form = cls(data, initial=asdict(record.config), auto_id=f'room-{record.slug}-%s')
        if record.backup_zone_slug is None:
            for field in BACKUP_FIELDS:
                del form.fields[field]
        return form
