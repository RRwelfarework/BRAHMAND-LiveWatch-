# BRAHMAND LiveWatch

An independent full-stack Earth-monitoring dashboard with a FastAPI backend and a Leaflet-based web interface. It uses public feeds for earthquakes, natural events, weather, and orbital data. No demonstration events are presented as observations.

## Requirements

- Python 3.10+
- Internet access for the public data feeds, map tiles, and the Leaflet/font CDNs

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. The API schema is available at <http://127.0.0.1:8000/docs>.

## Data sources and authenticity

| Module | Source | Label |
| --- | --- | --- |
| Earthquakes (past 24 hours) | [USGS GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | LIVE |
| Natural events | [NASA EONET](https://eonet.gsfc.nasa.gov/) | LIVE |
| Weather for a map-selected location | [Open-Meteo](https://open-meteo.com/) | LIVE |
| Space-station TLEs | [CelesTrak GP data](https://celestrak.org/NORAD/documentation/gp-data-formats.php), with [TLE API](https://tle.ivanstanojevic.me/) fallback; propagated with SGP4 | DELAYED / DERIVED |

“LIVE” means the data was retrieved from the named public feed; the feed itself may have a publication delay. Satellite locations are estimates from published TLEs at retrieval time, not direct radar observations. If CelesTrak is unavailable, the service uses the TLE API fallback and identifies it in the panel and report. TLE records are cached for five minutes and records older than seven days are excluded. The app does not generate simulated event data.

Earthquake alert levels are a magnitude-based heuristic, not official USGS alert declarations. NASA EONET does not provide a common severity scale, so those events are shown as unclassified.

The browser refreshes feeds every 60 seconds. Individual source failures are shown in the corresponding panel. The alert centre keeps a bounded event history in browser local storage. High-severity notifications require browser permission. Mission reports download as JSON.
