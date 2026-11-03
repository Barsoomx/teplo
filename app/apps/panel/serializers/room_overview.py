from rest_framework import serializers

from apps.panel.usecases.get_panel_overview import RoomOverview


class RoomOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    floor = serializers.CharField()
    zones = serializers.SerializerMethodField()
    devices = serializers.ListField(child=serializers.CharField())

    def get_zones(self, room: RoomOverview) -> list[str]:
        return [zone.slug for zone in room.zones]
