from rest_framework import serializers

from apps.panel.usecases.get_panel_overview import RoomOverview


class RoomOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    floor = serializers.CharField()
    zones = serializers.SerializerMethodField()
    devices = serializers.ListField(child=serializers.CharField())
    comfort_target = serializers.DecimalField(
        source='comfort.comfort_target',
        max_digits=4,
        decimal_places=1,
        allow_null=True,
    )
    setback_target = serializers.DecimalField(
        source='comfort.setback_target',
        max_digits=4,
        decimal_places=1,
        allow_null=True,
    )
    air_sensor = serializers.CharField(source='comfort.air_sensor', allow_null=True)
    backup_zone = serializers.CharField(source='comfort.backup_zone', allow_null=True)
    backup_live = serializers.SerializerMethodField()
    backup_active = serializers.SerializerMethodField()
    reason = serializers.CharField(source='intent.reason', allow_null=True)
    target = serializers.DecimalField(source='intent.target', max_digits=4, decimal_places=1, allow_null=True)
    until = serializers.DateTimeField(source='intent.until', allow_null=True)
    guest_room = serializers.BooleanField()
    adjustable = serializers.BooleanField()
    occupancy = serializers.SerializerMethodField()
    follows_occupancy = serializers.BooleanField()

    def get_zones(self, room: RoomOverview) -> list[str]:
        return [zone.slug for zone in room.zones]

    def get_backup_live(self, room: RoomOverview) -> bool:
        return room.comfort is not None and room.comfort.backup_live

    def get_backup_active(self, room: RoomOverview) -> bool:
        return room.comfort is not None and room.comfort.backup_active

    def get_occupancy(self, room: RoomOverview) -> str | None:
        return None if room.occupancy is None else room.occupancy.state.value
