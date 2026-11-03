from rest_framework import serializers


class LoadOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField()
    zone = serializers.CharField(allow_null=True)
    device = serializers.CharField()
    priority = serializers.IntegerField()
    phase = serializers.CharField()
    phase_origin = serializers.CharField()
    learned_phase = serializers.CharField(allow_null=True)
    learned_amps = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    learning_samples = serializers.IntegerField()
    nominal_amps = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    amps = serializers.DecimalField(max_digits=4, decimal_places=1)
    amps_origin = serializers.CharField()
    state = serializers.CharField()
    live = serializers.BooleanField()
