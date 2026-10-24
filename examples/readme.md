# Example responses

The two `response_SIMULATED_*.json` files were produced by the **real Django server over HTTP**, with the
[OpenRouteService](https://account.heigit.org/) call replaced by a simulated route (straight lines between real city coordinates, distance
multiplied by 1.15). The fuel prices and the station selection are real (from the supplied CSV); the route
geometry and distance are **not** real ORS output. Run the API with your own `ORS_API_KEY` to get real routes.

They were generated with `"include_geometry": false` to keep them short.
