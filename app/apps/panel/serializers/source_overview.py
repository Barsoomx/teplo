from rest_framework import serializers


class SourceOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    room = serializers.CharField(allow_null=True)
    kind = serializers.CharField()
    device = serializers.CharField(allow_null=True)
    priority = serializers.IntegerField(allow_null=True)
    live = serializers.BooleanField()
    available = serializers.BooleanField()
    on = serializers.BooleanField(allow_null=True)
    setpoint = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    level = serializers.CharField(allow_null=True)
    temperature = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    target_setpoint = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    manual_until = serializers.DateTimeField(source='held_until', allow_null=True)
    shed = serializers.BooleanField()
    lit_until = serializers.DateTimeField(allow_null=True)
    burning = serializers.BooleanField()
