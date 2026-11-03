from django.contrib import admin
from django.http import HttpRequest

from apps.climate.models import ClimateSettings, ComfortPeriod, Zone, ZoneLearning
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
        'heating_enabled',
        'live_control',
        'air_control_live',
    )
    list_editable = ('heating_enabled', 'live_control', 'air_control_live')
    readonly_fields = ('boost_until', 'last_command_at', 'last_dry_run_at', 'last_air_dry_run_at')
    inlines = (ComfortPeriodInline,)

    def get_readonly_fields(self, request: HttpRequest, obj: Zone | None = None) -> tuple[str, ...]:
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj is None or not Load.objects.filter(zone=obj, shed_at__isnull=False).exists():
            return fields

        return (*fields, *SHED_ZONE_FIELDS)


@admin.register(ClimateSettings)
class ClimateSettingsAdmin(admin.ModelAdmin):
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
    list_display = ('zone', 'early_stop_offset', 'dead_time', 'heat_up_rates', 'mornings', 'updated_at')

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request: HttpRequest, obj: ZoneLearning | None = None) -> bool:  # noqa: ARG002
        return False

    def has_delete_permission(self, request: HttpRequest, obj: ZoneLearning | None = None) -> bool:  # noqa: ARG002
        return False
