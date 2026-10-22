from urllib.parse import quote, urlencode

from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.serializers import FuelStopSerializer, RouteRequestSerializer
from routing.services.errors import (
    LocationError,
    RouteNotFound,
    RoutingServiceError,
    Unreachable,
)
from routing.services.planner import plan_route


class RouteView(APIView):
    """Driving route plus the fuel stops that keep the trip cheap."""

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, payload):
        serializer = RouteRequestSerializer(data=payload)
        if not serializer.is_valid():
            return Response(
                {"error": _validation_message(serializer.errors), "fields": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        start = serializer.validated_data["start"]
        end = serializer.validated_data["end"]
        try:
            result = plan_route(start, end)
        except LocationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Unreachable as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except RouteNotFound as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except RoutingServiceError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        result["stops"] = FuelStopSerializer(result["stops"], many=True).data
        query = urlencode({"start": start, "end": end}, quote_via=quote)
        result["map_url"] = request.build_absolute_uri(f"/map/?{query}")
        return Response(result)


def map_page(request):
    start = (request.GET.get("start") or "New York, NY").strip()
    end = (request.GET.get("end") or "Los Angeles, CA").strip()
    return render(
        request,
        "routing/map.html",
        {"start": start, "end": end},
    )


def _validation_message(errors):
    non_field = errors.get("non_field_errors")
    if non_field:
        return str(non_field[0])
    missing = [name for name, problems in errors.items() if problems]
    if missing:
        return "Give both a start and an end, like 'Chicago, IL'."
    return "Invalid request."
