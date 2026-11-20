const REFRESH_MS = 60_000;
const HISTORY_KEY = "brahmand-livewatch-history-v1";
const map = L.map("map", { zoomControl: true, worldCopyJump: true }).setView([20, 10], 2);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 8,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

const layers = {
  earthquake: L.layerGroup().addTo(map),
  disaster: L.layerGroup().addTo(map),
  satellite: L.layerGroup().addTo(map),
};
let currentFilter = "all";
let quakeEvents = [];
let disasterEvents = [];
let satelliteData = [];
let satelliteSource = "CelesTrak / SGP4";
let refreshInProgress = false;
let history = readHistory();
let historyLoadedFromServer = history.length > 0;
let toastTimer;

function readHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
}

function formatTime(value) {
  if (!value) return "TIME N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "TIME N/A" : date.toISOString().slice(11, 16) + " UTC";
}

function formatDateTime(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "N/A" : date.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

function severityClass(severity) {
  return severity === "critical" || severity === "high" ? "high" : severity === "moderate" ? "moderate" : severity === "low" ? "low" : "unknown";
}

async function getFeed(url) {
  const response = await fetch(url);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function setFeedError(elementId, error) {
  const element = document.getElementById(elementId);
  if (element) element.innerHTML = `<div class="empty-state">Feed unavailable: ${escapeHtml(error.message)}</div>`;
}

function updateMap() {
  Object.values(layers).forEach((layer) => layer.clearLayers());
  quakeEvents.forEach((event) => {
    const radius = Math.max(4, Math.min(13, (Number(event.magnitude) || 1) * 1.8));
    const marker = L.circleMarker([event.latitude, event.longitude], {
      radius, className: "quake-marker", color: "#ff7166", fillColor: "#ff7166", fillOpacity: .78, weight: 1,
    });
    marker.bindPopup(`<b>${escapeHtml(event.title)}</b><br>Magnitude ${escapeHtml(event.magnitude ?? "N/A")} · ${escapeHtml(event.place || "")}`);
    marker.addTo(layers.earthquake);
  });
  disasterEvents.filter((event) => event.latitude != null && event.longitude != null).forEach((event) => {
    L.circleMarker([event.latitude, event.longitude], {
      radius: 5, className: "event-marker", color: "#efb75b", fillColor: "#efb75b", fillOpacity: .82, weight: 1,
    }).bindPopup(`<b>${escapeHtml(event.title)}</b><br>${escapeHtml((event.categories || []).join(", "))}`)
      .addTo(layers.disaster);
  });
  satelliteData.forEach((satellite) => {
    L.circleMarker([satellite.latitude, satellite.longitude], {
      radius: 3, className: "sat-marker", color: "#70c9ff", fillColor: "#70c9ff", fillOpacity: .9, weight: 1,
    }).bindPopup(`<b>${escapeHtml(satellite.name)}</b><br>Estimated position · ${escapeHtml(Math.round(satellite.altitude_km))} km`)
      .addTo(layers.satellite);
  });
  Object.entries(layers).forEach(([name, layer]) => {
    if (currentFilter === "all" || currentFilter === name) layer.addTo(map);
    else map.removeLayer(layer);
  });
}

function renderQuakes(events) {
  document.getElementById("quake-count").textContent = events.length.toLocaleString();
  const rows = events.slice(0, 8).map((event) => `
    <tr>
      <td>${escapeHtml(event.magnitude == null ? "—" : Number(event.magnitude).toFixed(1))}</td>
      <td>${event.url ? `<a class="quake-link" href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">${escapeHtml(event.place || event.title)}</a>` : escapeHtml(event.place || event.title)}</td>
      <td>${event.depth_km == null ? "—" : `${Number(event.depth_km).toFixed(0)} km`}</td>
      <td>${formatTime(event.time)}</td>
    </tr>`).join("");
  document.getElementById("quake-rows").innerHTML = rows || '<tr><td colspan="4" class="empty-state">No events in this feed.</td></tr>';
}

function renderDisasters(events) {
  document.getElementById("event-count").textContent = events.length.toLocaleString();
  const html = events.slice(0, 9).map((event) => `
    <div class="event-row"><div><div class="event-name">${event.url ? `<a class="quake-link" href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">${escapeHtml(event.title)}</a>` : escapeHtml(event.title)}</div><span class="event-category">${escapeHtml((event.categories || []).join(" · ") || "Natural event")}</span></div><time class="event-time">${formatTime(event.time)}</time></div>`).join("");
  document.getElementById("event-list").innerHTML = html || '<div class="empty-state">No open events reported.</div>';
}

function renderSatellites(items) {
  document.getElementById("satellite-count").textContent = items.length.toLocaleString();
  const html = items.slice(0, 10).map((item) => `
    <div class="satellite-row"><span class="sat-name">${escapeHtml(item.name)}</span><span class="sat-coord">${Number(item.latitude).toFixed(1)}°, ${Number(item.longitude).toFixed(1)}°</span></div>`).join("");
  document.getElementById("satellite-list").innerHTML = html || '<div class="empty-state">No orbital objects returned.</div>';
}

async function loadHistory() {
  const data = await getFeed("/api/history?limit=100");
  const events = Array.isArray(data.events) ? data.events : [];
  const previousIds = new Set(history.map((event) => event.id));
  const newlySeen = historyLoadedFromServer
    ? events.filter((event) => !previousIds.has(event.id))
    : [];
  history = events;
  historyLoadedFromServer = true;
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  } catch {
    showToast("Event history could not be saved in this browser.");
  }
  return newlySeen;
}

function renderAlerts() {
  const items = history.slice(0, 12);
  document.getElementById("alert-total").textContent = history.length.toString();
  document.getElementById("alert-list").innerHTML = items.length ? items.map((event) => `
    <div class="alert-item"><i class="sev ${severityClass(event.severity)}"></i><div><div class="alert-title">${escapeHtml(event.title)}</div><div class="alert-meta">${escapeHtml(event.source)} · ${escapeHtml(event.place)}${event.severity === "unclassified" ? " · severity unavailable" : ""}</div></div><time class="alert-time">${formatTime(event.time)}</time></div>`).join("") : '<div class="empty-state">No event history yet.</div>';
}

function notifyNewEvents(events) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  events.filter((event) => event.severity === "high" || event.severity === "critical").slice(0, 3).forEach((event) => {
    new Notification(`BRAHMAND alert · ${event.source}`, { body: event.title });
  });
}

async function loadEarthquakes() {
  try {
    const data = await getFeed("/api/earthquakes");
    quakeEvents = data.events || [];
    renderQuakes(quakeEvents);
  } catch (error) {
    setFeedError("quake-rows", error);
    document.getElementById("quake-count").textContent = "—";
  }
}

async function loadDisasters() {
  try {
    const data = await getFeed("/api/disasters");
    disasterEvents = data.events || [];
    renderDisasters(disasterEvents);
  } catch (error) {
    setFeedError("event-list", error);
    document.getElementById("event-count").textContent = "—";
  }
}

async function loadSatellites() {
  try {
    const data = await getFeed("/api/satellites?limit=30");
    satelliteData = data.satellites || [];
    satelliteSource = data.source || "Satellite TLEs / SGP4";
    document.getElementById("satellite-source").textContent = satelliteSource.toUpperCase();
    document.getElementById("satellite-note").textContent = data.position_note;
    renderSatellites(satelliteData);
  } catch (error) {
    setFeedError("satellite-list", error);
    document.getElementById("satellite-count").textContent = "—";
  }
}

async function refreshFeeds() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  const button = document.getElementById("refresh-button");
  button.disabled = true;
  try {
    await Promise.all([loadEarthquakes(), loadDisasters(), loadSatellites()]);
    updateMap();
    let newEvents = [];
    try {
      newEvents = await loadHistory();
      renderAlerts();
    } catch (error) {
      setFeedError("alert-list", error);
    }
    notifyNewEvents(newEvents);
    document.getElementById("updated-at").textContent = `UPDATED ${new Date().toISOString().slice(11, 19)} UTC`;
  } finally {
    refreshInProgress = false;
    button.disabled = false;
  }
}

async function loadWeather(latitude, longitude) {
  document.getElementById("weather-detail").textContent = `${latitude.toFixed(2)}°, ${longitude.toFixed(2)}°`;
  document.getElementById("weather-value").textContent = "…";
  try {
    const data = await getFeed(`/api/weather?latitude=${encodeURIComponent(latitude)}&longitude=${encodeURIComponent(longitude)}`);
    const current = data.current;
    if (!current) throw new Error("Weather feed returned no current conditions.");
    document.getElementById("weather-value").textContent = `${Math.round(current.temperature_2m)}°C`;
    document.getElementById("weather-detail").textContent = `Feels ${Math.round(current.apparent_temperature)}° · wind ${Math.round(current.wind_speed_10m)} km/h`;
  } catch (error) {
    document.getElementById("weather-value").textContent = "N/A";
    document.getElementById("weather-detail").textContent = `Unavailable: ${error.message}`;
  }
}

function exportReport() {
  const report = {
    title: "BRAHMAND LiveWatch Mission Report",
    generated_at: new Date().toISOString(),
    authenticity_note: "Public feed data is live as retrieved. Satellite positions are SGP4 estimates from published TLEs, not direct observations. No simulated data is included.",
    summary: {
      earthquakes_last_24h: quakeEvents.length,
      active_natural_events: disasterEvents.length,
      satellite_positions_estimated: satelliteData.length,
    },
    sources: {
      earthquakes: "USGS all-day GeoJSON",
      natural_events: "NASA EONET open events",
      satellite_positions: satelliteSource,
      weather: "Open-Meteo at last selected map location",
    },
    event_history: history,
    satellite_estimates: satelliteData,
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `brahmand-mission-report-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(link);
  link.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
    link.remove();
  }, 1000);
}

map.on("click", (event) => loadWeather(event.latlng.lat, event.latlng.lng));
document.querySelectorAll(".filter-chip").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".filter-chip").forEach((chip) => chip.classList.remove("active"));
    button.classList.add("active");
    currentFilter = button.dataset.layer;
    updateMap();
  });
});
document.getElementById("refresh-button").addEventListener("click", refreshFeeds);
document.getElementById("export-button").addEventListener("click", exportReport);
document.getElementById("notify-button").addEventListener("click", async () => {
  if (!("Notification" in window)) {
    showToast("Browser notifications are not supported here.");
    return;
  }
  const permission = await Notification.requestPermission();
  showToast(permission === "granted" ? "High-severity browser alerts enabled." : "Notification permission was not granted.");
});

function updateClock() {
  document.getElementById("utc-clock").textContent = `${new Date().toISOString().slice(11, 19)} UTC`;
}
updateClock();
setInterval(updateClock, 1000);
renderAlerts();
refreshFeeds();
setInterval(refreshFeeds, REFRESH_MS);
