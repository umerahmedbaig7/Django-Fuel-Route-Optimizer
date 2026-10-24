import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-set-DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = ["rest_framework", "planner"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# OpenStreetMap's tile servers require a Referer header from web pages; Django's default
# ("same-origin") would strip it on requests to another site.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# The service is stateless: fuel prices come from the CSV and routes are cached in memory.
# There are no models, so `migrate` is optional and creates nothing of substance.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "planner.exceptions.api_exception_handler",
    # Protects the shared free ORS quota (40 requests/minute, 2000/day).
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("API_THROTTLE_RATE", "30/min")},
}

# ---- Routing service (OpenRouteService) ----
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
ORS_BASE_URL = os.environ.get("ORS_BASE_URL", "https://api.openrouteservice.org").rstrip("/")
ORS_TIMEOUT = (5, 30)  # (connect, read) seconds
ORS_SNAP_RADIUS_METERS = 5000  # how far a start/finish point may be moved onto a road
ORS_SIMPLIFY_GEOMETRY = True
GEOCODE_CACHE_SECONDS = 7 * 24 * 3600
ROUTE_CACHE_SECONDS = 6 * 3600

# ---- Fuel planning ----
FUEL_CSV_PATH = os.environ.get("FUEL_CSV_PATH", str(BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"))
VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
# A station ID can appear on several rows with different prices; collapse them with this rule.
PRICE_AGGREGATION = "mean"  # "mean", "min" or "max"
MIN_VALID_PRICE = 1.0
MAX_VALID_PRICE = 10.0
# Stations farther than this from the route are ignored. The planner tries the smallest
# value first and only widens the corridor when no feasible plan exists.
CORRIDOR_BUFFERS_MILES = (10, 25, 40)
ROUTE_SAMPLE_MILES = 2.0
# A stop is kept only if skipping it would cost at least this many dollars more.
# 0 gives the pure minimum-cost plan, which can include stops that save a few cents.
MIN_STOP_SAVING_USD = 1.00

# ---- Map page (/api/route/map/) ----
# OSM's public tile server is for light use. Point MAP_TILE_URL at another provider for heavy traffic.
MAP_TILE_URL = os.environ.get("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = os.environ.get(
    "MAP_TILE_ATTRIBUTION", '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {"planner": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO"), "propagate": False}},
}
