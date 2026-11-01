from rest_framework import serializers


class PlugOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    available = serializers.BooleanField()
    error_code = serializers.CharField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    on = serializers.BooleanField(allow_null=True)
    power_watts = serializers.DecimalField(max_digits=7, decimal_places=1, allow_null=True)
    current_amps = serializers.DecimalField(max_digits=6, decimal_places=3, allow_null=True)
    voltage_volts = serializers.DecimalField(max_digits=5, decimal_places=1, allow_null=True)
