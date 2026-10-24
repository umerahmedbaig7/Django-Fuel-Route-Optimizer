from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services import plan_trip
from .stations import get_stations


class RoutePlanView(APIView):
    """POST {"start": "...", "finish": "..."} -> route, fuel stops and cost."""

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(plan_trip(data["start"], data["finish"], data["include_geometry"]))


class HealthView(APIView):
    """Confirms the fuel data loads and whether a routing key is configured."""

    throttle_classes = []

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "stations_loaded": len(get_stations()),
                "routing_api_key_configured": bool(settings.ORS_API_KEY),
            }
        )


class MapPageView(View):
    """HTML page: a form plus a Leaflet map. It calls POST /api/route/ from the browser and draws the result.

    Optional query string: ?start=...&finish=... fills the form and plans immediately.
    """

    def get(self, request):
        config = {
            "routeUrl": reverse("route-plan"),
            "tileUrl": settings.MAP_TILE_URL,
            "tileAttribution": settings.MAP_TILE_ATTRIBUTION,
        }
        return render(request, "planner/map.html", {"config": config})
