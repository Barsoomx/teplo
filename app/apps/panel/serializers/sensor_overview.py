from rest_framework import serializers


class SensorOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField()
    temperature = serializers.DecimalField(max_digits=None, decimal_places=1, allow_null=True)
    humidity = serializers.DecimalField(max_digits=None, decimal_places=1, allow_null=True)
    available = serializers.BooleanField()
    age_seconds = serializers.IntegerField(allow_null=True)
