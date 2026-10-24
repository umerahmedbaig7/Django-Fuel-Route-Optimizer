from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, help_text='Place name such as "Chicago, IL", or "lat,lon".')
    finish = serializers.CharField(max_length=200)
    include_geometry = serializers.BooleanField(default=True, required=False)

    def validate(self, attrs):
        if attrs["start"].strip().lower() == attrs["finish"].strip().lower():
            raise serializers.ValidationError("start and finish must be different locations.")
        return attrs
