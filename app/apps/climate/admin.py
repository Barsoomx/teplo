from django.contrib import admin

from apps.climate.models import ComfortPeriod, Zone


class ComfortPeriodInline(admin.TabularInline):
    model = ComfortPeriod
    extra = 0


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'kind', 'thermostat', 'heating_enabled', 'live_control')
    list_editable = ('heating_enabled', 'live_control')
    inlines = (ComfortPeriodInline,)
