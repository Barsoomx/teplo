from django.contrib import admin
from django.http import HttpRequest

from apps.power.models import Outage


@admin.register(Outage)
class OutageAdmin(admin.ModelAdmin):
    list_display = ('began_at', 'ended_at', 'cause', 'cause_marked_at')
    list_filter = ('cause',)

    def has_delete_permission(self, request: HttpRequest, obj: Outage | None = None) -> bool:
        return (obj is None or obj.boot_id is None) and super().has_delete_permission(request, obj)
