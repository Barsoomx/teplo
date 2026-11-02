from django.contrib import admin
from django.http import HttpRequest

from apps.climate.models import ClimateSettings, ComfortPeriod, Zone


class ComfortPeriodInline(admin.TabularInline):
    model = ComfortPeriod
    extra = 0


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'kind', 'thermostat', 'air_sensor', 'heating_enabled', 'live_control')
    list_editable = ('heating_enabled', 'live_control')
    inlines = (ComfortPeriodInline,)


@admin.register(ClimateSettings)
class ClimateSettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'outdoor_sensor', 'second_floor_sensor')

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        return not ClimateSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: ClimateSettings | None = None) -> bool:  # noqa: ARG002
        return False
