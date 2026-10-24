from django.urls import path

from .views import HealthView, MapPageView, RoutePlanView

urlpatterns = [
    path("route/", RoutePlanView.as_view(), name="route-plan"),
    path("route/map/", MapPageView.as_view(), name="route-map"),
    path("health/", HealthView.as_view(), name="health"),
]
