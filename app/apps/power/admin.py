from django.contrib import admin

from apps.power.models import Outage


@admin.register(Outage)
class OutageAdmin(admin.ModelAdmin):
    list_display = ('began_at', 'ended_at', 'cause', 'cause_marked_at')
    list_filter = ('cause',)
