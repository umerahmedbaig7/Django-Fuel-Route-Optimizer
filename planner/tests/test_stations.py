import math

from django.test import SimpleTestCase, override_settings

from planner.exceptions import FuelDataUnavailable
from planner.stations import _load_cached, get_stations, load_stations, locate_city

from .fixtures import temp_dir, write_csv

REAL_CSV = "data/fuel-prices-for-be-assessment.csv"


class RealDatasetTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stations, cls.report = load_stations(REAL_CSV)

    def test_row_accounting_matches_the_file(self):
        r = self.report
        self.assertEqual(r.rows_read, 8151)
        self.assertEqual(r.non_us_rows, 620)
        self.assertEqual(r.invalid_price_rows, 0)
        self.assertEqual(r.stations_without_location, 0)
        # every row is either dropped, merged into another row, or becomes a station
        self.assertEqual(r.rows_read - r.non_us_rows - r.duplicate_rows_merged, r.stations_loaded)

    def test_stations_are_unique_priced_and_inside_the_contiguous_us(self):
        self.assertEqual(len({s.station_id for s in self.stations}), len(self.stations))
        for s in self.stations:
            self.assertTrue(1.0 <= s.price <= 10.0, s)
            self.assertTrue(24.3 <= s.lat <= 49.6 and -125.0 <= s.lon <= -66.5, s)

    def test_canadian_provinces_are_excluded(self):
        self.assertFalse({s.state for s in self.stations} & {"ON", "QC", "BC", "AB"})


class LoaderCleaningTests(SimpleTestCase):
    def load(self, rows, **kwargs):
        with temp_dir() as d:
            return load_stations(write_csv(d, rows), **kwargs)

    def test_invalid_prices_are_dropped_and_counted(self):
        rows = [
            (1, "A", "I-80", "Chicago", "IL", "3.50"),
            (2, "B", "I-80", "Chicago", "IL", ""),
            (3, "C", "I-80", "Chicago", "IL", "abc"),
            (4, "D", "I-80", "Chicago", "IL", "0"),
            (5, "E", "I-80", "Chicago", "IL", "-3.2"),
            (6, "F", "I-80", "Chicago", "IL", "99"),
            (7, "G", "I-80", "Chicago", "IL", "nan"),
            (8, "H", "I-80", "Chicago", "IL", "inf"),
        ]
        stations, report = self.load(rows)
        self.assertEqual([s.station_id for s in stations], [1])
        self.assertEqual(report.invalid_price_rows, 7)

    def test_duplicate_ids_are_merged_with_the_configured_rule(self):
        rows = [(9, "A", "I-80", "Dallas", "TX", "3.00"), (9, "A", "I-80", "Dallas", "TX", "3.40")]
        self.assertAlmostEqual(self.load(rows)[0][0].price, 3.20)
        self.assertAlmostEqual(self.load(rows, aggregation="min")[0][0].price, 3.00)
        self.assertAlmostEqual(self.load(rows, aggregation="max")[0][0].price, 3.40)
        self.assertEqual(self.load(rows)[1].duplicate_rows_merged, 1)

    def test_non_us_rows_are_dropped(self):
        rows = [(1, "A", "HWY 401", "Toronto", "ON", "4.10"), (2, "B", "I-80", "Chicago", "IL", "3.50")]
        stations, report = self.load(rows)
        self.assertEqual([s.station_id for s in stations], [2])
        self.assertEqual(report.non_us_rows, 1)

    def test_whitespace_is_cleaned(self):
        stations, _ = self.load([(1, "  PILOT   #1 ", "I-8,   EXIT 119", "  Gila  Bend ", "az", "3.9")])
        s = stations[0]
        self.assertEqual((s.name, s.address, s.city, s.state), ("PILOT #1", "I-8, EXIT 119", "Gila Bend", "AZ"))

    def test_unknown_city_is_dropped_and_counted(self):
        rows = [(1, "A", "I-80", "Nowhereville", "TX", "3.5"), (2, "B", "I-80", "Dallas", "TX", "3.5")]
        stations, report = self.load(rows)
        self.assertEqual([s.station_id for s in stations], [2])
        self.assertEqual(report.stations_without_location, 1)

    def test_missing_file_raises_clear_error(self):
        with self.assertRaisesMessage(FuelDataUnavailable, "not found"):
            load_stations("/nonexistent/fuel.csv")

    def test_missing_column_raises_clear_error(self):
        with temp_dir() as d:
            path = write_csv(d, [], header="ID,Name,City,State\n")
            with self.assertRaisesMessage(FuelDataUnavailable, "missing columns"):
                load_stations(path)

    def test_file_with_no_usable_rows_raises(self):
        with self.assertRaisesMessage(FuelDataUnavailable, "no usable"):
            self.load([(1, "A", "I-80", "Chicago", "IL", "abc")])


class LocateCityTests(SimpleTestCase):
    def test_known_city_is_near_its_real_location(self):
        lat, lon = locate_city("Dallas", "TX")
        self.assertAlmostEqual(lat, 32.78, delta=0.15)
        self.assertAlmostEqual(lon, -96.80, delta=0.15)

    def test_spelling_variants_resolve_to_the_same_point(self):
        self.assertEqual(locate_city("Saint Louis", "MO"), locate_city("St. Louis", "MO"))
        self.assertIsNotNone(locate_city("De Forest", "WI"))

    def test_unknown_city_returns_none(self):
        self.assertIsNone(locate_city("Nowhereville", "TX"))

    def test_no_city_is_placed_at_a_placeholder_coordinate(self):
        for city, state in [("Bristol", "VA"), ("Livingston", "TX")]:
            lat, lon = locate_city(city, state)
            self.assertTrue(24 < lat < 50 and -125 < lon < -66, (city, lat, lon))


class GetStationsTests(SimpleTestCase):
    def test_uses_the_configured_path_and_caches(self):
        _load_cached.cache_clear()
        with temp_dir() as d:
            path = write_csv(d, [(1, "A", "I-80", "Chicago", "IL", "3.5")])
            with override_settings(FUEL_CSV_PATH=str(path)):
                first = get_stations()
                self.assertIs(first, get_stations())
        self.assertEqual(len(first), 1)
        _load_cached.cache_clear()

    def test_price_is_finite(self):
        for s in load_stations(REAL_CSV)[0]:
            self.assertTrue(math.isfinite(s.price))
