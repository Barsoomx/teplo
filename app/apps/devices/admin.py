from django.contrib import admin

from apps.devices.models import Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'kind', 'driver', 'monitor_only')
    list_filter = ('kind', 'driver')
    search_fields = ('slug', 'name')
