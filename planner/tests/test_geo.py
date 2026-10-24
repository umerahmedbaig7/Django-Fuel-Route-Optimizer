from django.test import SimpleTestCase

from planner.geo import RouteIndex, haversine_miles, sample_route
from planner.stations import locate_city

LINE = [(-100.0 + i * 0.1, 40.0) for i in range(11)]  # ~1 degree of longitude at 40N, about 53 miles


class HaversineTests(SimpleTestCase):
    def test_chicago_to_dallas_is_about_800_miles_as_the_crow_flies(self):
        a, b = locate_city("Chicago", "IL"), locate_city("Dallas", "TX")
        self.assertAlmostEqual(haversine_miles(*a, *b), 802, delta=15)

    def test_zero_distance(self):
        self.assertEqual(haversine_miles(40, -100, 40, -100), 0)


class SampleRouteTests(SimpleTestCase):
    def test_markers_start_at_zero_and_end_at_the_reported_distance(self):
        points = sample_route(LINE, 120.0, step_miles=2.0)
        self.assertEqual(points[0].mile, 0.0)
        self.assertAlmostEqual(points[-1].mile, 120.0)
        self.assertEqual((points[-1].lon, points[-1].lat), (LINE[-1][0], LINE[-1][1]))

    def test_markers_increase_and_spacing_is_bounded(self):
        points = sample_route(LINE, 120.0, step_miles=2.0)
        gaps = [b.mile - a.mile for a, b in zip(points, points[1:])]
        scale = 120.0 / (10 * haversine_miles(40, -100, 40, -99.9))  # reported distance / polyline length
        self.assertTrue(all(g > 0 for g in gaps))
        self.assertLessEqual(max(gaps), 2.0 * scale + 1e-6)

    def test_single_point_route(self):
        self.assertEqual(len(sample_route([(-100.0, 40.0), (-100.0, 40.0)], 0.0)), 1)


class RouteIndexTests(SimpleTestCase):
    def setUp(self):
        self.points = sample_route(LINE, 53.0, step_miles=1.0)
        self.index = RouteIndex(self.points)

    def test_finds_nearest_point_and_distance(self):
        # 5 miles north of the middle of the route
        lat = 40.0 + 5 / 69.05
        point, miles = self.index.nearest(lat, -99.5, max_miles=10)
        self.assertAlmostEqual(miles, 5.0, delta=0.8)
        self.assertAlmostEqual(point.mile, 26.5, delta=1.5)

    def test_station_beyond_the_limit_is_ignored(self):
        self.assertIsNone(self.index.nearest(40.0 + 30 / 69.05, -99.5, max_miles=10))

    def test_station_far_outside_the_route_bbox_is_ignored(self):
        self.assertIsNone(self.index.nearest(35.0, -90.0, max_miles=40))
