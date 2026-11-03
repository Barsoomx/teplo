from django.contrib import admin
from django.http import HttpRequest

from apps.devices.models import Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'kind', 'driver', 'monitor_only', 'room')
    list_filter = ('kind', 'driver', 'room')
    search_fields = ('slug', 'name')

    def get_readonly_fields(self, request: HttpRequest, obj: Device | None = None) -> tuple[str, ...]:
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj is None or not (obj.zones.exists() or obj.air_zones.exists()):
            return fields

        return (*fields, 'room')
