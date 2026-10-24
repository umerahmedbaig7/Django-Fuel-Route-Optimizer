from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler


class PlannerError(APIException):
    """Base class. Every subclass sets an HTTP status and a stable machine-readable code."""


class LocationNotFound(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "location_not_found"
    default_detail = "The location could not be found."


class LocationOutsideCoverage(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "location_outside_coverage"
    default_detail = "The location is outside the contiguous USA."


class SameLocation(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "same_location"
    default_detail = "Start and finish resolve to the same place."


class RouteNotFound(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "route_not_found"
    default_detail = "No drivable route exists between the two locations."


class NoFeasibleFuelPlan(PlannerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "no_feasible_fuel_plan"
    default_detail = "The route cannot be driven within the vehicle's range using the available stations."


class FuelDataUnavailable(PlannerError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "fuel_data_unavailable"
    default_detail = "Fuel price data is missing or unusable."


class RoutingNotConfigured(PlannerError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "routing_not_configured"
    default_detail = "ORS_API_KEY is not set on the server."


class RoutingAuthFailed(PlannerError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "routing_auth_failed"
    default_detail = "The routing service rejected the server's API key."


class RoutingRateLimited(PlannerError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "routing_rate_limited"
    default_detail = "The routing service quota is exhausted. Try again later."


class RoutingTimeout(PlannerError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    default_code = "routing_timeout"
    default_detail = "The routing service timed out."


class RoutingUnavailable(PlannerError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "routing_unavailable"
    default_detail = "The routing service failed or returned an unexpected response."


def api_exception_handler(exc, context):
    """Return every error as {"error": {"code", "message", "details"?}}."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, ValidationError):
        error = {"code": "invalid_request", "message": "The request body is invalid.", "details": response.data}
    elif isinstance(exc, APIException):
        error = {"code": exc.default_code, "message": str(exc.detail)}
    else:
        error = {"code": "error", "message": str(response.data)}
    response.data = {"error": error}
    return response
