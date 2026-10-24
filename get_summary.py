import sys

import requests

start = sys.argv[1] if len(sys.argv) > 1 else "Chicago, IL"
finish = sys.argv[2] if len(sys.argv) > 2 else "Dallas, TX"
base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8000"

try:
    r = requests.post(f"{base}/api/route/", json={"start": start, "finish": finish, "include_geometry": False}, timeout=60)
except requests.RequestException as e:
    sys.exit(f"Could not reach {base}. Is the server running? ({e.__class__.__name__})")

data = r.json()
if r.status_code != 200:
    sys.exit(f"Error {r.status_code}: {data['error']['code']} - {data['error']['message']}")

route, s, b = data["route"], data["fuel_summary"], data["baseline_comparison"]
print(f"\nTrip: {data['start']['resolved_as']}  ->  {data['finish']['resolved_as']}")
print(f"Distance:            {route['distance_miles']:,.1f} miles ({route['duration_hours']:.1f} hours)")
print(f"Fuel stops:          {len(data['fuel_stops'])}")
print(f"Fuel burned:         {s['fuel_consumed_gallons']:.1f} gallons at {s['mpg']} mpg")
print(f"Paid at the pumps:   ${s['amount_paid_at_stops_usd']:,.2f}")
print(f"Total fuel cost:     ${s['total_fuel_cost_usd']:,.2f}  (includes the starting tank)")
if b:
    print(f"Saved vs. baseline:  ${b['optimized_saves_total_cost_usd']:,.2f}  (baseline: fill up at the farthest station)")

if data["fuel_stops"]:
    print("\n #  City                      Price/gal   Buy (gal)   Cost      Route mile")
    for x in data["fuel_stops"]:
        place = f"{x['city']}, {x['state']}"
        print(f"{x['stop_number']:>2}  {place:<25} ${x['price_per_gallon']:<9.3f} {x['gallons_purchased']:<11.2f} ${x['cost_usd']:<8.2f} {x['route_mile']}")
else:
    print("\nNo fuel stop needed: the trip is within the 500-mile range.")