# BRAHMAND LiveWatch

An independent full-stack Earth-monitoring dashboard with a FastAPI backend and a Leaflet-based web interface. It uses public feeds for earthquakes, natural events, weather, and orbital data. No demonstration events are presented as observations.

## Requirements

- Python 3.10+
- Internet access for the public data feeds, map tiles, and the Leaflet/font CDNs
- PostgreSQL for a hosted, persistent event history (SQLite is used automatically for local development)

## Repository layout

```text
.github/workflows/ci.yml  GitHub test workflow
static/                   Dashboard frontend (HTML, CSS, JavaScript)
tests/                    Backend and database tests
app.py                    FastAPI backend and API routes
database.py               Event database schema and persistence
render.yaml               Render web service and PostgreSQL blueprint
requirements.txt          Python dependencies
```

Do not upload `.venv/`, local database files, secrets, or generated archives. `.gitignore` excludes these local files.

## Run locally

```powershell
python --version
python -m venv .venv-livewatch
.\.venv-livewatch\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Use Python 3.10 or newer. Open <http://127.0.0.1:8000> after Uvicorn starts; the API schema is at <http://127.0.0.1:8000/docs>. Do not open `static/index.html` with VS Code Live Server: it serves only the frontend, so `/api/*` requests return 404. By default, the app creates `brahmand.db` locally. Set `DATABASE_URL` to a PostgreSQL connection string when you want to use PostgreSQL locally; never commit production database credentials.

Run the backend and database tests with:

```powershell
python -m unittest discover -s tests -v
```

## Deploy

This project includes a Render Blueprint in `render.yaml`. It deploys the FastAPI app (which serves the frontend and API together) and a PostgreSQL database in the same region. To deploy it:

1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint** and connect the GitHub repository.
3. Confirm the web service and database resources, then deploy. Render supplies the database URL to the backend without storing credentials in GitHub.
4. Open the Render service URL. The dashboard, `/api/*` endpoints, and shared event history are served by the same service. `/api/health` checks database connectivity.

GitHub stores the source code and runs tests on pushes and pull requests; it does not host the Python backend or database. GitHub Pages cannot run this app as-is. The included free Render database is for testing: it expires after 30 days, and the free web service can sleep while idle. Upgrade the database to a paid plan before relying on long-term event history. The service also needs outbound internet access to retrieve public data feeds.

## Data sources and authenticity

| Module | Source | Label |
| --- | --- | --- |
| Earthquakes (past 24 hours) | [USGS GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | LIVE |
| Natural events | [NASA EONET](https://eonet.gsfc.nasa.gov/) | LIVE |
| Weather for a map-selected location | [Open-Meteo](https://open-meteo.com/) | LIVE |
| Space-station TLEs | [CelesTrak GP data](https://celestrak.org/NORAD/documentation/gp-data-formats.php), with [TLE API](https://tle.ivanstanojevic.me/) fallback; propagated with SGP4 | DELAYED / DERIVED |

“LIVE” means the data was retrieved from the named public feed; the feed itself may have a publication delay. Satellite locations are estimates from published TLEs at retrieval time, not direct radar observations. If CelesTrak is unavailable, the service uses the TLE API fallback and identifies it in the panel and report. TLE records are cached for five minutes and records older than seven days are excluded. The app does not generate simulated event data.

Earthquake alert levels are a magnitude-based heuristic, not official USGS alert declarations. NASA EONET does not provide a common severity scale, so those events are shown as unclassified.

The browser refreshes feeds every 60 seconds. Individual source failures are shown in the corresponding panel. USGS and NASA events are deduplicated and stored in the shared database; the alert centre displays the most recent 100 records. High-severity notifications require browser permission. Mission reports download as JSON.
