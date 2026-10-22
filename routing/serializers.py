from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=100, trim_whitespace=True)
    end = serializers.CharField(max_length=100, trim_whitespace=True)

    def validate(self, attrs):
        if attrs["start"].casefold() == attrs["end"].casefold():
            raise serializers.ValidationError("Start and end are the same place.")
        return attrs


class FuelStopSerializer(serializers.Serializer):
    opis_id = serializers.CharField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    price_per_gallon = serializers.FloatField()
    gallons = serializers.FloatField()
    cost = serializers.FloatField()
    mile_marker = serializers.FloatField()
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    miles_off_route = serializers.FloatField()
