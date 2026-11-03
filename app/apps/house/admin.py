from django.contrib import admin

from apps.house.models import Floor, Room


@admin.register(Floor)
class FloorAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'level')


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'floor', 'position', 'aliases')
    list_filter = ('floor',)
