from rest_framework import serializers


class ZoneOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField()
    mode = serializers.CharField()
    available = serializers.BooleanField()
    error_code = serializers.CharField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    floor_temperature = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    power = serializers.BooleanField(allow_null=True)
    setpoint = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    target_setpoint = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    boosted = serializers.BooleanField()
    live_control = serializers.BooleanField()
