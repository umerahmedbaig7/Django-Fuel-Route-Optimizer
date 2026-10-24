"""Choose where to refuel and how much to buy.

Model
-----
* The tank holds `tank` gallons (range / mpg) and starts full.
* A station sits `mile` miles along the route and `offset` miles away from it. Visiting it
  adds a round trip of 2 * offset miles, which burns fuel and counts against the range.
* Driving from one stop to the next costs: offset(from) + route distance between them + offset(to).

Algorithm
---------
1. `choose_stops`: dynamic programming over (station, quarter-gallons in the tank) picks the
   cheapest sequence of stops. Fuel burned is rounded up to the next quarter gallon, so the
   result is never optimistic about range.
2. `price_stops`: for that sequence, buy at each stop only what is needed to reach the next
   cheaper stop (or the destination), or fill the tank if none is within range. This is the
   exact, fractional-gallon purchase plan.
3. `plan_optimal` then drops any stop whose removal raises the exact cost by less than
   `min_stop_saving`, so a stop survives only if it is worth stopping for.
"""
import math
from dataclasses import dataclass

EPS = 1e-6
INF = float("inf")
STEPS_PER_GALLON = 4  # the DP tracks the tank in quarter gallons


@dataclass(frozen=True)
class Candidate:
    station: object
    mile: float  # where along the route the station's nearest point is
    offset_miles: float  # straight-line distance from the route to the station


@dataclass(frozen=True)
class PlannedStop:
    candidate: Candidate
    miles_from_previous: float  # driven distance since the previous stop (or the start), detours included
    arrival_gallons: float
    gallons_bought: float
    cost: float


@dataclass
class FuelPlan:
    stops: list
    final_leg_miles: float
    route_miles: float
    detour_miles: float
    driven_miles: float
    tank_gallons: float
    gallons_consumed: float
    gallons_purchased: float
    leftover_gallons: float
    reference_price: float
    amount_paid: float  # cash spent at the pumps
    initial_tank_value: float  # the starting tank, valued at the reference price
    leftover_value: float  # fuel still in the tank at the destination
    total_cost: float  # cost of the fuel actually burned = initial + paid - leftover


def find_candidates(index, stations, max_offset_miles):
    """Stations within `max_offset_miles` of the route, sorted by position along it."""
    found = []
    for station in stations:
        hit = index.nearest(station.lat, station.lon, max_offset_miles)
        if hit:
            point, miles = hit
            found.append(Candidate(station, point.mile, miles))
    found.sort(key=lambda c: (c.mile, c.station.price))
    return found


def cheapest_per_location(candidates):
    """Stations in the same town share a position and detour, so only the cheapest can ever win."""
    best = {}
    for c in candidates:
        key = (round(c.mile, 3), round(c.offset_miles, 3))
        if key not in best or c.station.price < best[key].station.price:
            best[key] = c
    return sorted(best.values(), key=lambda c: (c.mile, c.station.price))


def _leg_miles(stops, route_miles):
    """Driven miles for each leg: start->stop1, stop1->stop2, ..., last stop->destination."""
    legs, mile, offset = [], 0.0, 0.0
    for c in stops:
        legs.append(offset + (c.mile - mile) + c.offset_miles)
        mile, offset = c.mile, c.offset_miles
    legs.append(offset + (route_miles - mile))
    return legs


def choose_stops(candidates, route_miles, tank_gallons, mpg):
    """Cheapest stop sequence, or None when the destination cannot be reached.

    Nodes are 0 = start, 1..n = stations, n+1 = destination. The tank level is tracked in
    quarter gallons ("units"). `arrive[j][g]` is the lowest cost to reach node j with g units
    left; `depart[j][g]` is the same after buying at j (any amount, at j's price).
    """
    n = len(candidates)
    mile = [0.0] + [c.mile for c in candidates] + [route_miles]
    offset = [0.0] + [c.offset_miles for c in candidates] + [0.0]
    price = [0.0] + [c.station.price / STEPS_PER_GALLON for c in candidates] + [0.0]  # USD per unit
    range_miles = tank_gallons * mpg
    full = int(tank_gallons * STEPS_PER_GALLON)
    miles_per_unit = mpg / STEPS_PER_GALLON

    arrive = [[INF] * (full + 1) for _ in range(n + 2)]
    arrive_from = [[None] * (full + 1) for _ in range(n + 2)]  # (previous node, its units on departure)
    depart = [[INF] * (full + 1) for _ in range(n + 2)]
    depart_from = [[0] * (full + 1) for _ in range(n + 2)]  # units on arrival it topped up from
    depart[0][full] = 0.0  # the starting tank is already paid for

    for j in range(1, n + 2):
        i = j - 1
        while i >= 0 and mile[j] - mile[i] <= range_miles:
            used = math.ceil((offset[i] + mile[j] - mile[i] + offset[j]) / miles_per_unit - EPS)
            if used <= full:
                for g in range(used, full + 1):
                    if depart[i][g] < arrive[j][g - used]:
                        arrive[j][g - used] = depart[i][g]
                        arrive_from[j][g - used] = (i, g)
            i -= 1
        if j <= n:
            # Buying (g - g0) units at j costs price[j] each: a running minimum over g0 does it in one pass.
            best, best_g = INF, 0
            for g in range(full + 1):
                value = arrive[j][g] - g * price[j]
                if value < best:
                    best, best_g = value, g
                if best < INF:
                    depart[j][g] = best + g * price[j]
                    depart_from[j][g] = best_g

    final_units = min(range(full + 1), key=lambda g: arrive[n + 1][g])
    if arrive[n + 1][final_units] == INF:
        return None

    chosen, node, units = [], n + 1, final_units
    while True:
        previous, departed = arrive_from[node][units]
        if previous == 0:
            break
        chosen.append(candidates[previous - 1])
        units = depart_from[previous][departed]
        node = previous
    chosen.reverse()
    return chosen


def _account(stops, buys, arrivals, route_miles, tank, mpg, reference_price, leftover):
    legs = _leg_miles(stops, route_miles)
    planned = [
        PlannedStop(c, legs[k], arrivals[k], buys[k], buys[k] * c.station.price) for k, c in enumerate(stops)
    ]
    detour = 2 * sum(c.offset_miles for c in stops)
    driven = sum(legs)
    paid = sum(p.cost for p in planned)
    bought = sum(buys)
    last_price = next((p.candidate.station.price for p in reversed(planned) if p.gallons_bought > EPS), reference_price)
    initial_value = tank * reference_price
    leftover_value = leftover * last_price
    return FuelPlan(
        stops=planned,
        final_leg_miles=legs[-1],
        route_miles=route_miles,
        detour_miles=detour,
        driven_miles=driven,
        tank_gallons=tank,
        gallons_consumed=driven / mpg,
        gallons_purchased=bought,
        leftover_gallons=leftover,
        reference_price=reference_price,
        amount_paid=paid,
        initial_tank_value=initial_value,
        leftover_value=leftover_value,
        total_cost=initial_value + paid - leftover_value,
    )


def price_stops(stops, route_miles, tank, mpg, reference_price):
    """Exact gallons to buy at each stop of a fixed sequence. Raises ValueError if the sequence is not drivable."""
    legs = _leg_miles(stops, route_miles)
    fuel = float(tank)
    buys, arrivals = [], []
    for k, stop in enumerate(stops):
        fuel -= legs[k] / mpg
        if fuel < -EPS:
            raise ValueError(f"Cannot reach stop {k + 1}: leg of {legs[k]:.1f} miles exceeds the fuel on board.")
        arrivals.append(max(fuel, 0.0))
        need = 0.0
        for m in range(k + 1, len(stops)):
            need += legs[m] / mpg
            if stops[m].station.price < stop.station.price:
                break
        else:
            need += legs[len(stops)] / mpg
        buy = max(0.0, min(need, tank) - fuel)
        buys.append(buy)
        fuel += buy
    fuel -= legs[-1] / mpg
    if fuel < -EPS:
        raise ValueError("Cannot reach the destination from the last stop.")
    return _account(stops, buys, arrivals, route_miles, tank, mpg, reference_price, max(fuel, 0.0))


def plan_optimal(candidates, route_miles, tank, mpg, reference_price, min_stop_saving=0.0):
    """Cheapest FuelPlan, or None if no drivable plan exists.

    A stop is dropped when skipping it costs less than `min_stop_saving` dollars extra.
    """
    chosen = choose_stops(candidates, route_miles, tank, mpg)
    if chosen is None:
        return None
    plan = price_stops(chosen, route_miles, tank, mpg, reference_price)
    improved = True
    while improved:
        improved = False
        for k in range(len(plan.stops)):
            without = [s.candidate for i, s in enumerate(plan.stops) if i != k]
            try:
                alternative = price_stops(without, route_miles, tank, mpg, reference_price)
            except ValueError:
                continue  # that stop is needed to stay within range
            if alternative.total_cost <= plan.total_cost + min_stop_saving + EPS:
                plan, improved = alternative, True
                break
    return plan


def plan_baseline(candidates, route_miles, tank, mpg, reference_price):
    """Simple comparison: drive to the farthest reachable station, fill the tank, repeat.

    Ignores prices when choosing where to stop. Returns None if it gets stuck.
    """
    capacity_miles = tank * mpg
    fuel_miles = capacity_miles
    mile, offset, first = 0.0, 0.0, 0
    stops, buys, arrivals = [], [], []
    while offset + (route_miles - mile) > fuel_miles + EPS:
        pick = None
        for j in range(first, len(candidates)):
            c = candidates[j]
            if c.mile - mile > fuel_miles:
                break
            if offset + (c.mile - mile) + c.offset_miles <= fuel_miles + EPS:
                pick = j
        if pick is None:
            return None
        c = candidates[pick]
        fuel_miles -= offset + (c.mile - mile) + c.offset_miles
        arrivals.append(fuel_miles / mpg)
        buys.append((capacity_miles - fuel_miles) / mpg)
        fuel_miles = capacity_miles
        stops.append(c)
        mile, offset, first = c.mile, c.offset_miles, pick + 1
    leftover = (fuel_miles - (offset + (route_miles - mile))) / mpg
    return _account(stops, buys, arrivals, route_miles, tank, mpg, reference_price, max(leftover, 0.0))


def largest_gap(candidates, route_miles):
    """(start_mile, end_mile) of the longest stretch with no candidate station."""
    marks = [0.0] + [c.mile for c in candidates] + [route_miles]
    return max(zip(marks, marks[1:]), key=lambda pair: pair[1] - pair[0])
