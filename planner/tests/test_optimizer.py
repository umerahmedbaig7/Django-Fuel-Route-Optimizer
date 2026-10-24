from django.test import SimpleTestCase

from planner.geo import RouteIndex, haversine_miles, sample_route
from planner.optimizer import (
    Candidate,
    cheapest_per_location,
    choose_stops,
    find_candidates,
    largest_gap,
    plan_baseline,
    plan_optimal,
    price_stops,
)
from planner.stations import Station

TANK, MPG, REF = 50.0, 10, 3.50


def cand(mile, price, offset=0.0, sid=None):
    sid = sid or int(mile * 10 + price * 1000)
    return Candidate(Station(sid, f"S{sid}", "addr", "City", "TX", price, 40.0, -100.0), float(mile), float(offset))


def plan(cands, miles, **kw):
    return plan_optimal(sorted(cands, key=lambda c: c.mile), miles, TANK, MPG, REF, **kw)


class IdentityMixin:
    """Numbers in a FuelPlan must be consistent with each other."""

    def assertConsistent(self, p):
        self.assertAlmostEqual(p.gallons_consumed, p.driven_miles / MPG)
        self.assertAlmostEqual(p.driven_miles, p.route_miles + p.detour_miles)
        self.assertAlmostEqual(TANK + p.gallons_purchased - p.leftover_gallons, p.gallons_consumed, places=6)
        self.assertAlmostEqual(p.amount_paid, sum(s.cost for s in p.stops))
        self.assertAlmostEqual(p.total_cost, p.initial_tank_value + p.amount_paid - p.leftover_value)
        self.assertAlmostEqual(p.driven_miles, sum(s.miles_from_previous for s in p.stops) + p.final_leg_miles)
        for s in p.stops:
            self.assertLessEqual(s.miles_from_previous, TANK * MPG + 1e-6)
            self.assertLessEqual(s.arrival_gallons + s.gallons_bought, TANK + 1e-6)
        self.assertLessEqual(p.final_leg_miles, TANK * MPG + 1e-6)


class ShortRouteTests(SimpleTestCase, IdentityMixin):
    def test_route_under_range_needs_no_stops(self):
        p = plan([cand(100, 3.0)], 300)
        self.assertEqual(p.stops, [])
        self.assertAlmostEqual(p.gallons_consumed, 30.0)
        self.assertAlmostEqual(p.leftover_gallons, 20.0)
        self.assertEqual(p.amount_paid, 0)
        self.assertAlmostEqual(p.total_cost, 30.0 * REF)  # only the fuel burned is charged
        self.assertConsistent(p)

    def test_route_of_exactly_the_range_needs_no_stops(self):
        self.assertEqual(plan([], 500).stops, [])

    def test_stop_that_buys_nothing_is_not_reported(self):
        self.assertEqual(plan([cand(100, 2.0)], 400).stops, [])


class MultiStopTests(SimpleTestCase, IdentityMixin):
    def test_long_route_gets_multiple_stops_within_range(self):
        cands = [cand(m, 3.30) for m in range(100, 1200, 100)]
        p = plan(cands, 1200)
        self.assertGreaterEqual(len(p.stops), 2)
        self.assertAlmostEqual(p.gallons_consumed, 120.0)
        self.assertConsistent(p)

    def test_cheaper_stations_win_and_hand_computed_cost_matches(self):
        # 900 miles. Reaching 900 needs two stops; the $4.00 stop is never worth using.
        p = plan([cand(200, 4.00), cand(300, 3.00), cand(450, 3.50)], 900)
        self.assertEqual([s.candidate.mile for s in p.stops], [300, 450])
        self.assertAlmostEqual(p.stops[0].gallons_bought, 30.0)  # fill to full
        self.assertAlmostEqual(p.stops[1].gallons_bought, 10.0)  # just enough to finish
        self.assertAlmostEqual(p.amount_paid, 30 * 3.00 + 10 * 3.50)  # $125
        self.assertAlmostEqual(p.leftover_gallons, 0.0)
        self.assertConsistent(p)

    def test_buys_only_enough_to_reach_a_cheaper_stop(self):
        # 700 miles. A (mile 300, $3.00) is needed; B (mile 650, $2.99) is within range of A.
        p = plan([cand(300, 3.00), cand(650, 2.99)], 700, min_stop_saving=0.0)
        self.assertEqual(len(p.stops), 2)
        self.assertAlmostEqual(p.stops[0].gallons_bought, 15.0)  # 350 miles to B = 35 gal, 20 on board
        self.assertAlmostEqual(p.stops[1].gallons_bought, 5.0)
        self.assertConsistent(p)

    def test_min_stop_saving_drops_a_stop_that_saves_pennies(self):
        p = plan([cand(300, 3.00), cand(650, 2.99)], 700, min_stop_saving=1.0)
        self.assertEqual([s.candidate.mile for s in p.stops], [300])
        self.assertAlmostEqual(p.amount_paid, 20 * 3.00)
        self.assertConsistent(p)


class DetourTests(SimpleTestCase, IdentityMixin):
    # 700 miles; one station at mile 400 either on the route or 30 miles off it (60-mile round trip).
    def test_slightly_cheaper_station_is_skipped_when_the_detour_costs_more(self):
        p = plan([cand(400, 3.00, 0, sid=1), cand(400, 2.90, 30, sid=2)], 700)
        self.assertEqual(p.stops[0].candidate.station.station_id, 1)
        self.assertEqual(p.detour_miles, 0)
        self.assertConsistent(p)

    def test_much_cheaper_station_is_worth_the_detour(self):
        p = plan([cand(400, 3.00, 0, sid=1), cand(400, 2.00, 30, sid=2)], 700)
        self.assertEqual(p.stops[0].candidate.station.station_id, 2)
        self.assertEqual(p.detour_miles, 60)
        self.assertAlmostEqual(p.driven_miles, 760)
        self.assertConsistent(p)


class RangeLimitTests(SimpleTestCase, IdentityMixin):
    def test_station_exactly_at_max_range_is_usable(self):
        p = plan([cand(500, 3.0)], 800)
        self.assertEqual([s.candidate.mile for s in p.stops], [500])
        self.assertAlmostEqual(p.stops[0].miles_from_previous, 500)
        self.assertConsistent(p)

    def test_station_just_beyond_range_is_unreachable(self):
        self.assertIsNone(plan([cand(501, 3.0)], 800))

    def test_detour_counts_against_range(self):
        # mile 495 plus 10 miles off the route is 505 miles of driving: not reachable
        self.assertIsNone(plan([cand(495, 3.0, 10)], 800))

    def test_gap_longer_than_range_is_infeasible_and_located(self):
        cands = [cand(100, 3.0)]
        self.assertIsNone(plan(cands, 1000))
        self.assertEqual(largest_gap(cands, 1000), (100.0, 1000.0))

    def test_no_stations_on_a_long_route_is_infeasible(self):
        self.assertIsNone(plan([], 600))

    def test_every_leg_stays_within_range_on_a_long_route(self):
        p = plan([cand(m, 3.0 + (m % 7) * 0.05) for m in range(50, 3000, 45)], 3000)
        self.assertConsistent(p)
        self.assertGreaterEqual(len(p.stops), 5)

    def test_choose_stops_respects_a_smaller_tank(self):
        chosen = choose_stops([cand(m, 3.0) for m in range(100, 900, 100)], 900, 20.0, MPG)  # 200-mile range
        legs = [b.mile - a.mile for a, b in zip([cand(0, 0)] + chosen, chosen + [cand(900, 0)])]
        self.assertTrue(all(leg <= 200 for leg in legs))


class PricingTests(SimpleTestCase):
    def test_price_stops_rejects_an_undrivable_sequence(self):
        with self.assertRaises(ValueError):
            price_stops([cand(700, 3.0)], 900, TANK, MPG, REF)

    def test_cheapest_per_location_keeps_the_lowest_price(self):
        kept = cheapest_per_location([cand(100, 3.4, 2, sid=1), cand(100, 3.1, 2, sid=2), cand(100, 3.0, 5, sid=3)])
        self.assertEqual(sorted(c.station.station_id for c in kept), [2, 3])


class BaselineTests(SimpleTestCase, IdentityMixin):
    def test_baseline_fills_up_and_leaves_fuel_at_the_destination(self):
        cands = [cand(200, 4.00), cand(300, 3.00), cand(450, 3.50)]
        base = plan_baseline(cands, 900, TANK, MPG, REF)
        self.assertEqual([s.candidate.mile for s in base.stops], [450])  # farthest reachable, price ignored
        self.assertGreater(base.leftover_gallons, 0)
        self.assertGreater(base.amount_paid, base.total_cost - base.initial_tank_value)  # paid for fuel not burned
        self.assertConsistent(base)

    def test_optimized_plan_never_costs_more_than_the_baseline(self):
        cands = [cand(m, 3.0 + ((m * 7) % 13) * 0.05) for m in range(60, 2400, 55)]
        best = plan(cands, 2400)
        base = plan_baseline(sorted(cands, key=lambda c: c.mile), 2400, TANK, MPG, REF)
        self.assertLessEqual(best.total_cost, base.total_cost + 1e-6)
        self.assertConsistent(best)
        self.assertConsistent(base)

    def test_baseline_returns_none_when_stuck(self):
        self.assertIsNone(plan_baseline([cand(600, 3.0)], 1000, TANK, MPG, REF))


class FindCandidatesTests(SimpleTestCase):
    def test_stations_are_matched_to_route_position_and_detour(self):
        coords = [(-100.0 + i * 0.1, 40.0) for i in range(101)]  # ~530 miles east-west
        miles = haversine_miles(40, -100, 40, -90)
        index = RouteIndex(sample_route(coords, miles, 2.0))
        on = Station(1, "ON", "a", "c", "KS", 3.0, 40.0, -95.0)  # halfway, on the line
        off = Station(2, "OFF", "a", "c", "KS", 3.0, 40.0 + 8 / 69.05, -95.0)  # 8 miles north
        far = Station(3, "FAR", "a", "c", "KS", 3.0, 40.0 + 50 / 69.05, -95.0)
        found = {c.station.station_id: c for c in find_candidates(index, [on, off, far], 10)}
        self.assertEqual(set(found), {1, 2})
        self.assertAlmostEqual(found[1].mile, miles / 2, delta=2)
        self.assertAlmostEqual(found[2].offset_miles, 8, delta=0.8)
