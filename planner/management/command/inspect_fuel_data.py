import statistics

from django.conf import settings
from django.core.management.base import BaseCommand

from planner.stations import load_stations


class Command(BaseCommand):
    help = "Print what the fuel CSV contains and how many rows were dropped, merged or located."

    def handle(self, *args, **options):
        stations, report = load_stations(
            settings.FUEL_CSV_PATH, settings.PRICE_AGGREGATION, settings.MIN_VALID_PRICE, settings.MAX_VALID_PRICE
        )
        prices = [s.price for s in stations]
        out = self.stdout.write
        out(f"File:                          {settings.FUEL_CSV_PATH}")
        out(f"Rows read:                     {report.rows_read}")
        out(f"Non-US rows dropped:           {report.non_us_rows} ({', '.join(sorted(report.non_us_states))})")
        out(f"Rows with invalid price:       {report.invalid_price_rows}")
        out(f"Rows with invalid station ID:  {report.invalid_id_rows}")
        out(f"Duplicate-ID rows merged:      {report.duplicate_rows_merged} (price rule: {settings.PRICE_AGGREGATION})")
        out(f"Stations with no city match:   {report.stations_without_location}")
        out(f"Stations loaded:               {report.stations_loaded}")
        out(f"Price min / median / max:      ${min(prices):.3f} / ${statistics.median(prices):.3f} / ${max(prices):.3f}")
