import json
import re

from django.test import SimpleTestCase, override_settings


class MapPageTests(SimpleTestCase):
    def get(self):
        return self.client.get("/api/route/map/")

    def config(self, response):
        match = re.search(r'<script id="config" type="application/json">(.*?)</script>', response.content.decode(), re.S)
        return json.loads(match.group(1))

    def test_page_renders_with_leaflet_and_integrity_hashes(self):
        r = self.get()
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r["Content-Type"])
        self.assertIn("leaflet@1.9.4/dist/leaflet.js", html)
        self.assertIn("sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=", html)
        self.assertIn("sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=", html)

    def test_page_points_at_the_route_api_and_credits_openstreetmap(self):
        cfg = self.config(self.get())
        self.assertEqual(cfg["routeUrl"], "/api/route/")
        self.assertTrue(cfg["tileUrl"].startswith("https://"))
        self.assertIn("OpenStreetMap", cfg["tileAttribution"])

    @override_settings(MAP_TILE_URL="https://tiles.example.test/{z}/{x}/{y}.png")
    def test_tile_server_is_configurable(self):
        self.assertEqual(self.config(self.get())["tileUrl"], "https://tiles.example.test/{z}/{x}/{y}.png")

    def test_referrer_policy_lets_osm_tiles_see_a_referer(self):
        self.assertEqual(self.get()["Referrer-Policy"], "strict-origin-when-cross-origin")

    def test_page_never_builds_html_from_api_data(self):
        # station names and place labels come from outside; they must only be inserted as text
        script = self.get().content.decode().split("<script>")[-1]
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("document.write", script)

    def test_root_redirects_to_the_map_page(self):
        r = self.client.get("/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/api/route/map/"))

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post("/api/route/map/").status_code, 405)
