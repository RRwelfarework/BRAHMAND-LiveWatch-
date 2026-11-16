import unittest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app


class LiveWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        app._tle_cache = None
        self.client = TestClient(app.app)

    def test_health_and_frontend(self) -> None:
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_weather_rejects_out_of_range_coordinates(self) -> None:
        response = self.client.get("/api/weather?latitude=91&longitude=0")
        self.assertEqual(response.status_code, 422)

    def test_satellite_positions_are_earth_fixed_coordinates(self) -> None:
        now = datetime.now(timezone.utc)
        start_of_year = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        epoch_day = (now - start_of_year).total_seconds() / 86400 + 1
        tle_epoch = f"{now.year % 100:02d}{epoch_day:012.8f}"
        tle = (
            "ISS (ZARYA)\n"
            f"1 25544U 98067A   {tle_epoch}  .00010226  00000+0  19082-3 0  9993\n"
            "2 25544  51.6322 272.1219 0007375  28.0230 332.1155 15.49391288595180"
        )
        with patch.object(app, "fetch_tle_text", new=AsyncMock(return_value=tle)):
            response = self.client.get("/api/satellites?limit=1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        satellite = payload["satellites"][0]
        self.assertEqual(payload["authenticity"], "DELAYED / DERIVED")
        self.assertEqual(payload["source"], "CelesTrak / SGP4")
        self.assertGreaterEqual(satellite["latitude"], -90)
        self.assertLessEqual(satellite["latitude"], 90)
        self.assertGreaterEqual(satellite["longitude"], -180)
        self.assertLessEqual(satellite["longitude"], 180)

    def test_satellite_api_uses_real_tle_fallback_when_celestrak_is_blocked(self) -> None:
        now = datetime.now(timezone.utc)
        start_of_year = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        epoch_day = (now - start_of_year).total_seconds() / 86400 + 1
        tle_epoch = f"{now.year % 100:02d}{epoch_day:012.8f}"
        tle = {
            "member": [
                {
                    "satelliteId": 25544,
                    "name": "ISS (ZARYA)",
                    "line1": f"1 25544U 98067A   {tle_epoch}  .00010226  00000+0  19082-3 0  9993",
                    "line2": "2 25544  51.6322 272.1219 0007375  28.0230 332.1155 15.49391288595180",
                }
            ]
        }
        with (
            patch.object(
                app,
                "fetch_tle_text",
                new=AsyncMock(side_effect=HTTPException(502, "CelesTrak is blocked")),
            ),
            patch.object(app, "fetch_json", new=AsyncMock(return_value=tle)),
        ):
            response = self.client.get("/api/satellites?limit=5")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "TLE API (Ivan Stanojevic) / SGP4")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["satellites"][0]["norad_id"], 25544)

    def test_event_geometry_returns_point_or_approximate_center(self) -> None:
        point = app.event_coordinates({"type": "Point", "coordinates": [12, 34]})
        polygon = app.event_coordinates(
            {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}
        )
        self.assertEqual(point, (34.0, 12.0))
        self.assertEqual(polygon, (1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
