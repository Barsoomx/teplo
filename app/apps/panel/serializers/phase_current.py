from rest_framework import serializers


class PhaseCurrentSerializer(serializers.Serializer):
    phase = serializers.CharField()
    amps = serializers.DecimalField(max_digits=None, decimal_places=2, allow_null=True)
    stale = serializers.BooleanField()
    age_seconds = serializers.IntegerField(allow_null=True)
