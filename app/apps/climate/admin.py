from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db import OperationalError
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy

from apps.climate.domain.climate_config import start_offset_keeps_band
from apps.climate.models import ClimateSettings, ComfortPeriod, HeatSource, RoomClimate, Zone, ZoneLearning
from apps.climate.models.climate_settings import START_OFFSET_BELOW_MIN_BAND
from apps.climate.repositories.climate_settings import ClimateSettingsRepository
from apps.climate.usecases.reset_zone_learning import ResetZoneLearningInputDTO, ResetZoneLearningUseCase
from apps.devices.models import Device
from apps.power.models import Load

SHED_ZONE_FIELDS = ('slug', 'thermostat')
OVERRIDE_FIELDS = ('override_target', 'override_until', 'override_source')
GUARD_FIELDS = (
    'second_floor_sensor',
    'second_floor_guard_zone',
    'second_floor_guard_threshold',
    'second_floor_guard_hysteresis',
    'second_floor_guard_live',
)
AIR_SENSOR_OF_A_ZONE = gettext_lazy('This sensor is the air sensor of a zone')
AIR_SENSOR_IN_ANOTHER_ROOM = gettext_lazy('This sensor is placed in another room')
NAMES_SEPARATOR = ', '


class ResetOutcome(StrEnum):
    RESET = 'reset'
    UNLEARNED = 'unlearned'
    REFUSED = 'refused'


RESET_MESSAGES = (
    (ResetOutcome.RESET, gettext_lazy('Learning reset: %(zones)s'), messages.SUCCESS),
    (ResetOutcome.UNLEARNED, gettext_lazy('Nothing to reset: %(zones)s learned nothing yet'), messages.WARNING),
    (
        ResetOutcome.REFUSED,
        gettext_lazy('The database did not take the reset of %(zones)s: try again'),
        messages.ERROR,
    ),
)


class ComfortPeriodInline(admin.TabularInline):
    model = ComfortPeriod
    fk_name = 'zone'
    exclude = ('room_climate',)
    extra = 0


class RoomComfortPeriodInline(admin.TabularInline):
    model = ComfortPeriod
    fk_name = 'room_climate'
    exclude = ('zone',)
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

    def get_inlines(self, request: HttpRequest, obj: Zone | None) -> tuple[type[ComfortPeriodInline], ...]:  # noqa: ARG002
        if obj is not None and RoomClimate.objects.filter(room__zones=obj).exists():
            return ()

        return (ComfortPeriodInline,)

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


class RoomClimateAdminForm(forms.ModelForm):
    def clean(self) -> dict[str, Any]:
        super().clean()
        sensor = self.cleaned_data.get('air_sensor')
        if sensor is None:
            return self.cleaned_data

        if Zone.objects.filter(air_sensor=sensor).exists():
            self.add_error('air_sensor', AIR_SENSOR_OF_A_ZONE)
        elif sensor.room_id is not None and sensor.room_id != self.instance.room_id:
            self.add_error('air_sensor', AIR_SENSOR_IN_ANOTHER_ROOM)
        return self.cleaned_data


@admin.register(RoomClimate)
class RoomClimateAdmin(admin.ModelAdmin):
    form = RoomClimateAdminForm
    list_display = (
        'room',
        'comfort_target',
        'setback_target',
        'air_sensor',
        'backup_zone',
        'backup_below',
        'backup_live',
        'guest_room',
    )
    list_editable = ('backup_live',)
    readonly_fields = ('room', *OVERRIDE_FIELDS)
    inlines = (RoomComfortPeriodInline,)

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request: HttpRequest, obj: RoomClimate | None = None) -> bool:  # noqa: ARG002
        return False


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
    list_display = ('__str__', 'outdoor_sensor')
    exclude = GUARD_FIELDS
    readonly_fields = ('guests_until',)

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
    actions = ('reset_learning',)

    @admin.display(description='early-stop offset in effect')
    def early_stop_offset_in_effect(self, obj: ZoneLearning) -> Decimal:
        return ClimateSettingsRepository.control().regulation.early_stop_offset(obj.early_stop_offset)

    @admin.action(description=gettext_lazy('Reset learning'), permissions=('view',))
    def reset_learning(self, request: HttpRequest, queryset: QuerySet[ZoneLearning]) -> None:
        now = timezone.now()
        zones: dict[ResetOutcome, list[str]] = {outcome: [] for outcome in ResetOutcome}
        for learning in queryset.select_related('zone').order_by('zone__name'):
            zones[self._reset(learning.zone.slug, now)].append(learning.zone.name)
        for outcome, template, level in RESET_MESSAGES:
            if zones[outcome]:
                self.message_user(request, str(template) % {'zones': NAMES_SEPARATOR.join(zones[outcome])}, level)

    @staticmethod
    def _reset(zone: str, now: datetime) -> ResetOutcome:
        try:
            output = ResetZoneLearningUseCase.init().execute(ResetZoneLearningInputDTO(zone=zone, now=now))
        except OperationalError:
            return ResetOutcome.REFUSED

        return ResetOutcome.RESET if output.reset else ResetOutcome.UNLEARNED

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request: HttpRequest, obj: ZoneLearning | None = None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request: HttpRequest, obj: ZoneLearning | None = None) -> bool:  # noqa: ARG002
        return False


@admin.register(HeatSource)
class HeatSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'room', 'kind', 'device', 'priority', 'live', 'lit_until')
    list_editable = ('live',)
    readonly_fields = ('lit_until',)
