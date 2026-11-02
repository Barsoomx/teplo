from rest_framework import serializers


class OutageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    began_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField()
    cause = serializers.CharField()
    cause_marked_at = serializers.DateTimeField(allow_null=True)
