# Design notes (for explaining and defending the solution)

## 1. The task in plain language

Given two US locations, return the driving route and a refuelling plan for a vehicle that goes 500 miles on a
tank and does 10 mpg (so a full tank is 50 gallons). Fuel prices come from a CSV. Minimise what the trip costs
in fuel, without ever planning a stretch longer than 500 miles, and without driving far out of the way for a
cheap station.

## 2. What the CSV actually contains

Columns: `OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price`.

| Finding | Number | What I did |
|---|---|---|
| Rows | 8,151 | |
| **No latitude/longitude anywhere** | | Place stations from city + state (section 4) |
| Canadian rows (AB, BC, MB, NB, NS, ON, QC, SK, YT) | 620 | Dropped: the route is US-only |
| Station IDs appearing on several rows with different prices | 678 IDs; median spread 6¢, max 90¢ | Merged to one station, price = mean (configurable). The CSV does not say what the extra rows mean, so I did not guess a "right" one |
| Same ID with different address/city | 0 | So merging by ID is safe |
| Same ID with different names (`PILOT` vs `PILOT TRAVEL CENTER`) | 227 | First name kept |
| Whitespace noise in names, addresses, cities | hundreds | Collapsed |
| Prices | $2.687 to $6.399, median $3.399 | Valid band $1 to $10; nothing was outside it, but the filter exists and is tested |
| `Address` | Free text such as `I-44, EXIT 283 & US-69` | Not used for location (not geocodable without thousands of calls) |
| `Rack ID` | 306 distinct | Unused (wholesale price reference) |
| **Result** | **6,626 stations** | |

I did not invent any prices, stations, coordinates or columns. The only derived field is the coordinate, and
its source is stated.

## 3. Choosing the routing service

OpenRouteService (ORS) https://account.heigit.org/: one free account covers geocoding and routing, limits are published (directions
2,000/day, 40/min; geocoding 1,000/day), a key is needed, and attribution is required (the API returns it). The OSRM public demo needs no
key but is limited to reasonable non-commercial use at about 1 request/second with no guarantees, and does not geocode.
Details and links are in the README. I could not call ORS from my build environment, so its response format is taken from its
documentation, and I listed the unverified parts in the README.

**External calls per trip:** one directions call, plus one geocode each for start and finish (zero if the caller sends
`"lat,lon"`). Never one call per station: stations are filtered locally.

## 4. Putting stations on the map without coordinates

Options considered:

1. Geocode each station's address through a web service: about 6,600 calls against a 1,000/day geocoding quota. Rejected.
2. Use ORS or Nominatim on `City, State`: about 3,800 unique cities. Same problem.
3. **Offline lookup (chosen).** The `zipcodes` PyPI package bundles ZIP-code centroids. A station is placed at the
   mean centroid of the ZIP codes in its city. 100% of US stations matched (after fixing a bug where placeholder
   `0,0` ZIP rows polluted two cities).

Cost: location error. For cities with several ZIPs, 75% of stations are within about 3 miles of the centroid and 90%
within about 8.5 miles of it, and a truck stop on a highway exit can be farther from the town centre than that. So
`route_mile` and detour distances are estimates, and the response labels the precision as `city_zip_centroid`.

## 5. Finding stations near the route

* The route is a list of thousands of points. I resample it to a point every 2 miles and scale the mile markers
  so the last one equals ORS's own distance. That keeps all my numbers consistent with the distance I report.
* Putting the sampled points in a grid (0.25 degree cells) means each station only checks the few cells around it
  instead of every route point. This keeps it fast without numpy.
* For each station: nearest sampled point within 40 miles gives `route_mile` (position along the route) and
  `offset` (straight-line distance to the route). Then only those with offset <= 10 miles are used. If no feasible
  plan exists the corridor widens to 25, then 40 miles. This gives "stations near the route, not the cheapest regardless
  of detour".
* Stations in the same town share a position and a detour, so only the cheapest in each town can ever win. I keep
  only that one (about 300 stations become about 136 on New York to Los Angeles).

## 6. The optimisation

### Model

* The tank starts full (50 gallons). Fuel is consumed at 1 gallon per 10 miles.
* Visiting a station off the route adds a round trip of 2 x offset. That fuel is really burned, and it counts against the range.
* Driving from one stop to the next = `offset(from) + route distance between + offset(to)`. It must be <= 500 miles.
* At each stop you may buy any amount, up to a full tank, at that station's price.

### Step 1: choose the stops (dynamic programming)

State: (station, fuel in the tank). Fuel is tracked in quarter gallons, so there are 200 levels. For each station
and each fuel level, I store the cheapest cost to be there. From a station I can go to any later station within
range, which burns fuel (rounded **up** to the next quarter gallon so it never over-promises range). At a station I may buy fuel. The DP
fills a table, then walks backwards from the destination to recover the cheapest chain of stops. Measured on the simulated cross-country
route: about 0.02 s with the 10-mile corridor and 0.17 s with the 40-mile one.

Why DP instead of "go to the cheapest station in each 500-mile window"? The cheapest station may be 400 miles away
while a slightly dearer one 150 miles away lets you buy cheaply again soon after. Prices interact with range, and DP
handles that exactly.

### Step 2: exact gallons (greedy on the chosen stops)

For a fixed list of stops the best purchase rule is simple: at each stop buy just enough to reach the **next cheaper
stop** (or the destination); if no cheaper stop is within range, fill the tank. This is exact (fractions of a gallon), so reported
gallons and dollars are not rounded to the DP's quarter gallons.

### Step 3: drop pointless stops

A stop whose removal raises the exact cost by less than `MIN_STOP_SAVING_USD` ($1.00) is removed, provided the
trip remains within range. This removes stops that save pennies. Set it to 0 for the pure minimum-cost plan.
Small top-up stops that remain are required to stay within range.

## 7. Cost accounting (the "consumed vs paid" requirement)

Two different numbers exist, and the API reports both:

* `amount_paid_at_stops_usd`: money handed over at the pumps.
* `total_fuel_cost_usd`: the value of fuel actually burned on the trip.

They differ for two reasons:

1. **Fuel left at the destination.** If you fill the tank at the last stop, you pay for fuel you did not use. Paid exceeds consumed.
   The optimizer avoids this (it buys only what it needs), so it ends with about 0 gallons. The fill-up baseline does not.
2. **The starting tank.** I assume a full tank at departure. It was not bought at any listed station, so its price is unknown. I
   value it at the average price of stations near the route and say so in the response. Without this, long trips would
   look about $170 cheaper than they are.

Identity used in tests and in the response:

```
total_fuel_cost = initial_tank_value + amount_paid - left_at_destination_value
gallons: 50 (initial) + purchased - left_at_destination = consumed = total_driven_miles / 10
total_driven_miles = route_distance + detour_miles
```

Worked example from the simulated New York to Los Angeles run: consumed 290.83 gal = 2,908.3 miles / 10;
purchased 240.83 + 50 initial = 290.83; paid $728.91; initial tank 50 x $3.391 = $169.56; total = $898.47.

## 8. Baseline comparison

"Drive to the farthest station you can reach, fill the tank, repeat", ignoring prices. It uses the same candidate
stations (cheapest per town, so the baseline gets a small advantage and the savings are, if anything, understated). On
the simulated NY to LA run it makes 6 stops, pays $928.22, arrives with 21 gallons left, and its total consumed cost is $1,021.71
versus $898.47 for the optimized plan: about $123 less. Note the optimized plan makes *more* stops; fewer stops does not mean cheaper.

## 9. Trade-offs I would mention

| Decision | Why | Downside |
|---|---|---|
| City-centroid station locations | Free, offline, instant, 100% coverage | Several-mile error |
| Straight-line detours | No extra API calls | Underestimates road distance |
| DP in quarter gallons | Simple, fast, safe on range | Up to 2.5 miles of conservatism per leg |
| Full 500-mile range usable | Matches the brief | No safety reserve (lower `VEHICLE_RANGE_MILES` for one) |
| In-memory cache | No infrastructure | Per-process; use Redis with several workers |
| No database | Data is a static CSV | Updating prices means replacing the file |
| ORS over OSRM demo | One key, documented limits, includes geocoding | Needs signup; 40 requests/min |
