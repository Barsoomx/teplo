from rest_framework import serializers


class PresenceOverviewSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    room = serializers.CharField(allow_null=True)
    node = serializers.CharField()
    available = serializers.BooleanField()
    present = serializers.BooleanField(allow_null=True)
    age_seconds = serializers.IntegerField(allow_null=True)
    seen_age_seconds = serializers.IntegerField(allow_null=True)
    error_code = serializers.CharField(allow_null=True)
