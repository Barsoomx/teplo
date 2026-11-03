from decimal import Decimal
from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest

from apps.climate.domain.climate_config import start_offset_keeps_band
from apps.climate.models import ClimateSettings, ComfortPeriod, Zone, ZoneLearning
from apps.climate.models.climate_settings import START_OFFSET_BELOW_MIN_BAND
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.devices.models import Device
from apps.power.models import Load

SHED_ZONE_FIELDS = ('slug', 'thermostat')


class ComfortPeriodInline(admin.TabularInline):
    model = ComfortPeriod
    extra = 0


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = (
        'slug',
        'name',
        'kind',
        'thermostat',
        'air_sensor',
        'room',
        'heating_enabled',
        'live_control',
        'air_control_live',
    )
    list_editable = ('heating_enabled', 'live_control', 'air_control_live')
    list_filter = ('room',)
    readonly_fields = ('boost_until', 'last_command_at', 'last_dry_run_at', 'last_air_dry_run_at')
    inlines = (ComfortPeriodInline,)

    def get_readonly_fields(self, request: HttpRequest, obj: Zone | None = None) -> tuple[str, ...]:
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj is None or not Load.objects.filter(zone=obj, shed_at__isnull=False, shed_handed_back=False).exists():
            return fields

        return (*fields, *SHED_ZONE_FIELDS)

    def save_model(self, request: HttpRequest, obj: Zone, form: forms.ModelForm, change: bool) -> None:
        super().save_model(request, obj, form, change)
        Device.objects.filter(
            pk__in=[pk for pk in (obj.thermostat_id, obj.air_sensor_id) if pk is not None],
        ).update(room=obj.room_id)


class ClimateSettingsAdminForm(forms.ModelForm):
    def clean(self) -> dict[str, Any]:
        super().clean()
        start_offset = self.cleaned_data.get('air_start_offset')
        min_band = self.cleaned_data.get('air_min_band')
        if start_offset is not None and min_band is not None and not start_offset_keeps_band(start_offset, min_band):
            self.add_error('air_start_offset', START_OFFSET_BELOW_MIN_BAND)
        return self.cleaned_data


@admin.register(ClimateSettings)
class ClimateSettingsAdmin(admin.ModelAdmin):
    form = ClimateSettingsAdminForm
    list_display = (
        '__str__',
        'outdoor_sensor',
        'second_floor_sensor',
        'second_floor_guard_zone',
        'second_floor_guard_live',
    )

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return not ClimateSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: ClimateSettings | None = None) -> bool:  # noqa: ARG002
        return False


@admin.register(ZoneLearning)
class ZoneLearningAdmin(admin.ModelAdmin):
    list_display = (
        'zone',
        'early_stop_offset',
        'early_stop_offset_in_effect',
        'dead_time',
        'heat_up_rates',
        'mornings',
        'updated_at',
    )

    @admin.display(description='early-stop offset in effect')
    def early_stop_offset_in_effect(self, obj: ZoneLearning) -> Decimal:
        return ClimateSettingsRepository.control().regulation.early_stop_offset(obj.early_stop_offset)

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request: HttpRequest, obj: ZoneLearning | None = None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request: HttpRequest, obj: ZoneLearning | None = None) -> bool:  # noqa: ARG002
        return False
