/* TruckRoute web + Android WebView app. Same bundle ships in the APK. */
"use strict";

const API = (window.TRUCKROUTE_API || "").replace(/\/$/, "");
const NATIVE = typeof window.TruckRoute !== "undefined";
const $ = (id) => document.getElementById(id);

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), 2600);
}

/* ================= API client ================= */
const api = {
  access: localStorage.getItem("tr_token") || "",
  refresh: localStorage.getItem("tr_refresh") || "",
  setTokens(a, r) {
    this.access = a; this.refresh = r;
    localStorage.setItem("tr_token", a); localStorage.setItem("tr_refresh", r);
  },
  clear() {
    this.access = ""; this.refresh = "";
    localStorage.removeItem("tr_token"); localStorage.removeItem("tr_refresh");
  },
  async call(path, opts = {}, retry = true) {
    const res = await fetch(API + path, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        ...(this.access ? { Authorization: "Bearer " + this.access } : {}),
        ...(opts.headers || {}),
      },
    });
    if (res.status === 401 && retry && this.refresh) {
      const rr = await fetch(API + "/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: this.refresh }),
      });
      if (rr.ok) {
        const j = await rr.json();
        this.setTokens(j.access_token, j.refresh_token);
        return this.call(path, opts, false);
      }
      this.clear();
      showScreen("auth");
      throw new Error("Session expired. Please log in again.");
    }
    if (!res.ok) {
      let msg = "Request failed";
      try { msg = (await res.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    return ct.includes("json") ? res.json() : res.text();
  },
  get: (p) => api.call(p),
  post: (p, b) => api.call(p, { method: "POST", body: JSON.stringify(b || {}) }),
  put: (p, b) => api.call(p, { method: "PUT", body: JSON.stringify(b || {}) }),
  del: (p) => api.call(p, { method: "DELETE" }),
};

/* ================= screens ================= */
const SCREENS = ["auth", "map", "trucks", "saved", "trips", "account", "admin"];
function showScreen(name) {
  SCREENS.forEach((s) => ($("screen-" + s).hidden = s !== name));
  $("bottom-nav").hidden = name === "auth";
  document.querySelectorAll(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.screen === name)
  );
  if (name === "map") setTimeout(() => map && map.resize(), 50);
  if (name === "trucks") loadTrucks();
  if (name === "saved") loadSaved();
  if (name === "trips") loadTrips();
  if (name === "account") loadAccount();
  if (name === "admin") loadAdmin();
}
document.querySelectorAll(".nav-btn").forEach((b) =>
  b.addEventListener("click", () => showScreen(b.dataset.screen))
);

/* ================= auth ================= */
$("tab-login").onclick = () => {
  $("tab-login").classList.add("active"); $("tab-register").classList.remove("active");
  $("form-login").hidden = false; $("form-register").hidden = true; $("auth-error").textContent = "";
};
$("tab-register").onclick = () => {
  $("tab-register").classList.add("active"); $("tab-login").classList.remove("active");
  $("form-login").hidden = true; $("form-register").hidden = false; $("auth-error").textContent = "";
};
async function afterLogin(tokens) {
  api.setTokens(tokens.access_token, tokens.refresh_token);
  state.user = await api.get("/api/auth/me");
  $("nav-admin").hidden = !state.user.is_admin;
  await loadPrefs();
  showScreen("map");
  initMapOnce();
  startGps();
  startIncidentFeed();
}
$("btn-login").onclick = async () => {
  try {
    const t = await api.post("/api/auth/login", {
      email: $("login-email").value.trim(), password: $("login-password").value,
    });
    await afterLogin(t);
  } catch (e) { $("auth-error").textContent = e.message; }
};
$("btn-register").onclick = async () => {
  try {
    const t = await api.post("/api/auth/register", {
      display_name: $("reg-name").value.trim(),
      email: $("reg-email").value.trim(), password: $("reg-password").value,
    });
    await afterLogin(t);
  } catch (e) { $("auth-error").textContent = e.message; }
};

const state = {
  user: null, prefs: null, trucks: [], activeTruck: null,
  dest: null, stops: [], addingStop: false, // multi-stop waypoints
  route: null, routeId: null, selOption: 0,
  routeIncidents: [], // driver reports along the selected route (via /incidents/along)
  routeTraffic: null, // live TomTom traffic along the route (via /traffic/along); null = not fetched / disabled
  routeRests: [],     // rest/parking suggestions along the route
  restsBreakH: null,  // break interval (hrs) the rest suggestions were timed to
  navigating: false, sessionId: null,
  pos: null, // {lat, lng, speed, bearing}
  incidents: [], // latest nearby incident feed
};

/* ================= map ================= */
let map = null, mapStyle = "dark";
const STYLES = {
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  satellite: {
    version: 8,
    sources: {
      esri: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        attribution: "Imagery © Esri",
      },
    },
    layers: [{ id: "esri", type: "raster", source: "esri" }],
  },
};
function initMapOnce() {
  if (map) return;
  mapStyle = (state.prefs && state.prefs.map_style) || "dark";
  map = new maplibregl.Map({
    container: "map",
    style: STYLES[mapStyle] || STYLES.dark,
    center: [-83.4, 32.9], // central GA default until GPS locks
    zoom: 6,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "bottom-right");
  map.on("load", () => addRouteLayers());
  // Follow mode ends only on a REAL user pan/zoom gesture — v3.0 killed it
  // on any pointerdown anywhere (including HUD button taps), forever.
  map.on("dragstart", (e) => { if (e.originalEvent) followMode = false; });
  map.on("zoomstart", (e) => { if (e.originalEvent) followMode = false; });
}
function addRouteLayers() {
  if (!map.getSource("route")) {
    map.addSource("route", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-line", type: "line", source: "route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#3b82f6", "line-width": 7, "line-opacity": 0.95 } });
  }
  if (!map.getSource("route-slow")) {
    // Orange overlay where DRIVER-REPORTED slowdowns sit on the route.
    map.addSource("route-slow", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-slow-line", type: "line", source: "route-slow",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#f97316", "line-width": 9, "line-opacity": 0.9 } });
  }
  if (!map.getSource("route-live-slow")) {
    // Red overlay for LIVE TomTom slowdowns — a different source from
    // driver reports, colored differently on purpose.
    map.addSource("route-live-slow", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-live-slow-line", type: "line", source: "route-live-slow",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ef4444", "line-width": 9, "line-opacity": 0.95 } });
  }
  if (!map.getSource("route-done")) {
    map.addSource("route-done", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-done-line", type: "line", source: "route-done",
      paint: { "line-color": "#93a3bd", "line-width": 7, "line-opacity": 0.9 } });
  }
}
function setMapStyle(name) {
  mapStyle = name;
  const keep = state.route ? state.route.options[state.selOption] : null;
  map.setStyle(STYLES[name] || STYLES.dark);
  map.once("style.load", () => {
    addRouteLayers();
    if (keep) { drawRoute(keep.shape); drawSlowdowns(keep); drawLiveSlowdowns(keep); }
    if (gpsMarker) gpsMarker.addTo(map);
    if (destMarker) destMarker.addTo(map);
  });
}
$("btn-style").onclick = () => {
  const menu = $("style-menu");
  menu.hidden = !menu.hidden;
};
$("style-menu").querySelectorAll("button").forEach((b) => {
  b.onclick = () => {
    $("style-menu").hidden = true;
    if (b.dataset.style !== mapStyle) {
      setMapStyle(b.dataset.style);
      toast("Map: " + b.dataset.style);
    }
  };
});

let gpsMarker = null, destMarker = null, puckConeEl = null;

/* ---- GPS follow engine (v3.1) ----
 * Why v3.0 felt loose: native fixes were requested at 2000ms/5m AND the
 * page polled for them every 2000ms (up to ~4s of lag), the puck teleported
 * fix-to-fix, the camera re-centered in north-up jumps, and a pointerdown
 * ANYWHERE (even tapping a HUD button) permanently disabled follow mode.
 * v3.1: fixes stream in at ~1Hz (native push / watchPosition), a rAF loop
 * dead-reckons + eases the puck between fixes and snaps it to the route
 * line while navigating, and the camera follows with bearing rotation and
 * look-ahead. Follow mode now ends only on a real map pan/zoom gesture
 * and returns via the recenter button. */
const puck = {
  fix: null,      // latest raw fix {lat,lng,speed,bearing,time}
  disp: null,     // displayed (smoothed) {lat,lng}
  dispBrg: 0,     // displayed (smoothed) bearing, degrees
  lastFixAt: 0,   // performance.now() of latest fix
  raf: 0, _lastFrame: 0,
};
let followMode = true;
let lastCamAt = 0;

function onPos(fix) {
  if (!fix || fix.lat == null || fix.lng == null) return;
  if (fix.time && puck.fix && puck.fix.time && fix.time <= puck.fix.time) return; // stale/dup
  state.pos = { lat: fix.lat, lng: fix.lng, speed: fix.speed || 0, bearing: fix.bearing || 0 };
  puck.fix = { lat: fix.lat, lng: fix.lng, speed: fix.speed || 0,
               bearing: fix.bearing || 0, time: fix.time || Date.now() };
  puck.lastFixAt = performance.now();
  if (!puck.disp) {
    puck.disp = { lat: fix.lat, lng: fix.lng };
    puck.dispBrg = fix.bearing || 0;
    if (map && followMode && !state.navigating)
      map.flyTo({ center: [fix.lng, fix.lat], zoom: Math.max(map.getZoom(), 13) });
  }
  $("gps-banner").hidden = true;
  ensurePuckMarker();
  startPuckLoop();
  if (state.navigating) navUpdate();
}

function ensurePuckMarker() {
  if (!map || gpsMarker || !puck.disp) return;
  const el = document.createElement("div");
  el.className = "nav-puck";
  el.innerHTML = '<div class="nav-puck-cone"></div><div class="nav-puck-dot"></div>';
  puckConeEl = el.querySelector(".nav-puck-cone");
  gpsMarker = new maplibregl.Marker({ element: el })
    .setLngLat([puck.disp.lng, puck.disp.lat]).addTo(map);
}

function angLerp(a, b, t) {
  const d = ((b - a + 540) % 360) - 180;
  return (a + d * t + 360) % 360;
}
function destPoint(lat, lng, brgDeg, distM) {
  const R = 6371000, t = Math.PI / 180;
  const la1 = lat * t, lo1 = lng * t, br = brgDeg * t, dr = distM / R;
  const la2 = Math.asin(Math.sin(la1) * Math.cos(dr) + Math.cos(la1) * Math.sin(dr) * Math.cos(br));
  const lo2 = lo1 + Math.atan2(Math.sin(br) * Math.sin(dr) * Math.cos(la1),
                               Math.cos(dr) - Math.sin(la1) * Math.sin(la2));
  return { lat: la2 / t, lng: lo2 / t };
}

function startPuckLoop() {
  if (puck.raf) return;
  const step = () => {
    puck.raf = requestAnimationFrame(step);
    if (!map || !gpsMarker || !puck.disp || !puck.fix) return;
    const now = performance.now();
    const dt = puck._lastFrame ? Math.min(0.1, (now - puck._lastFrame) / 1000) : 0.016;
    puck._lastFrame = now;
    const f = puck.fix;
    // Dead-reckon the target from the last fix, then ease toward it.
    const ageS = Math.min(2.5, (now - puck.lastFixAt) / 1000);
    let tgt = (f.speed > 1 && f.bearing)
      ? destPoint(f.lat, f.lng, f.bearing, f.speed * ageS)
      : { lat: f.lat, lng: f.lng };
    // Snap to the route line while navigating (kills off-road GPS jitter).
    if (state.navigating && nav.coords.length) {
      const near = nearestOnRoute(tgt.lat, tgt.lng);
      if (near.dist < 30) { const c = nav.coords[near.idx]; tgt = { lat: c[1], lng: c[0] }; }
    }
    const k = 1 - Math.exp(-dt / 0.12);
    puck.disp = {
      lat: puck.disp.lat + (tgt.lat - puck.disp.lat) * k,
      lng: puck.disp.lng + (tgt.lng - puck.disp.lng) * k,
    };
    if (f.speed > 1 && f.bearing)
      puck.dispBrg = angLerp(puck.dispBrg, f.bearing, 1 - Math.exp(-dt / 0.25));
    gpsMarker.setLngLat([puck.disp.lng, puck.disp.lat]);
    if (puckConeEl)
      puckConeEl.style.transform = `rotate(${puck.dispBrg - map.getBearing()}deg)`;
    // Camera follow
    if (followMode && now - lastCamAt > 120) {
      lastCamAt = now;
      if (state.navigating) {
        const look = destPoint(puck.disp.lat, puck.disp.lng, puck.dispBrg, 70);
        map.easeTo({ center: [look.lng, look.lat], bearing: puck.dispBrg,
                     pitch: 55, zoom: 16.5, duration: 240, easing: (t) => t });
      } else {
        const c = map.getCenter();
        if (haversineM(c.lat, c.lng, puck.disp.lat, puck.disp.lng) > 25)
          map.easeTo({ center: [puck.disp.lng, puck.disp.lat], duration: 240, easing: (t) => t });
      }
    }
  };
  puck.raf = requestAnimationFrame(step);
}

$("btn-recenter").onclick = () => {
  if (!state.pos || !map) { toast("No GPS fix yet"); return; }
  followMode = true;
  lastCamAt = 0;
  if (!state.navigating)
    map.flyTo({ center: [state.pos.lng, state.pos.lat], zoom: 15 });
};

/* ================= GPS + voice ================= */
let watchId = null, pollTimer = null;
function startGps() {
  if (NATIVE) {
    try { window.TruckRoute.startGps(); } catch (e) {}
    pollTimer = setInterval(pollNativeGps, 1000); // fallback; pushes are primary
    pollNativeGps();
  } else if ("geolocation" in navigator) {
    watchId = navigator.geolocation.watchPosition(
      (p) => onPos({ lat: p.coords.latitude, lng: p.coords.longitude,
                     speed: p.coords.speed || 0, bearing: p.coords.heading || 0,
                     time: p.timestamp }),
      () => { $("gps-banner").hidden = false; $("gps-banner").textContent = "GPS unavailable — check location permission"; },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
    );
  } else {
    $("gps-banner").hidden = false;
    $("gps-banner").textContent = "Location not supported on this device";
  }
}
function pollNativeGps() {
  try {
    const raw = window.TruckRoute.getLastLocation();
    if (!raw) return;
    const p = JSON.parse(raw);
    if (!p || !p.lat) return;
    onPos(p); // onPos dedupes fixes already delivered by push
  } catch (e) {}
}
// Native bridge pushes each fix the moment it arrives (~1Hz).
window.TruckRouteNative = { onLocation: (p) => { try { onPos(p); } catch (e) {} } };
function speak(text) {
  if (state.prefs && state.prefs.voice_enabled === false) return;
  if (NATIVE) { try { window.TruckRoute.speak(text); return; } catch (e) {} }
  try {
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  } catch (e) {}
}

/* ================= incident reports (crowdsourced) ================= */
const INCIDENT_TYPES = [
  { kind: "police",        emoji: "🚔", label: "Police" },
  { kind: "accident",      emoji: "💥", label: "Accident" },
  { kind: "hazard",        emoji: "⚠️", label: "Hazard" },
  { kind: "closure",       emoji: "⛔", label: "Road closed" },
  { kind: "construction",  emoji: "🚧", label: "Construction" },
  { kind: "weigh_open",    emoji: "🟢", label: "Weigh stn open" },
  { kind: "weigh_closed",  emoji: "🔴", label: "Weigh stn closed" },
  { kind: "low_clearance", emoji: "↕️", label: "Low clearance" },
  { kind: "no_parking",    emoji: "🅿️", label: "No truck parking" },
  { kind: "traffic_jam",   emoji: "🐢", label: "Traffic jam" },
];
let incidentMarkers = [];
let incidentTimer = null;

function buildReportGrid() {
  const grid = $("report-grid");
  grid.innerHTML = "";
  INCIDENT_TYPES.forEach((t) => {
    const b = document.createElement("button");
    b.className = "report-btn";
    b.innerHTML = `<span class="e">${t.emoji}</span><span></span>`;
    b.querySelector("span:last-child").textContent = t.label;
    b.onclick = () => reportIncident(t.kind);
    grid.appendChild(b);
  });
}
$("btn-close-report").onclick = () => ($("modal-report").hidden = true);
$("btn-report").onclick = () => ($("modal-report").hidden = false);
$("btn-report-nav").onclick = () => ($("modal-report").hidden = false);

async function reportIncident(kind) {
  $("modal-report").hidden = true;
  // Report at GPS fix; fall back to map center so it still works indoors.
  let lat = null, lng = null;
  if (state.pos) { lat = state.pos.lat; lng = state.pos.lng; }
  else if (map) { const c = map.getCenter(); lat = c.lat; lng = c.lng; }
  if (lat == null) { toast("No location yet — wait for GPS"); return; }
  try {
    await api.post("/api/incidents", { kind, lat, lng });
    const t = INCIDENT_TYPES.find((x) => x.kind === kind);
    toast(`${t ? t.emoji : "📍"} Reported — thanks, driver`);
    refreshIncidents();
  } catch (e) { toast(e.message); }
}

function incidentPopupHtml(it) {
  const when = it.age_min < 1 ? "just now" : `${it.age_min} min ago`;
  return `<div class="incident-popup">
    <div class="t">${it.emoji} ${escapeHtml(it.label)}</div>
    <div class="s">${when} · 👍 ${it.confirms} · 👎 ${it.denies}</div>
    <div class="row">
      <button class="yes" onclick="incidentVote('${it.id}','confirm')">Still there</button>
      <button class="no" onclick="incidentVote('${it.id}','deny')">Gone</button>
    </div></div>`;
}
window.incidentVote = async (id, vote) => {
  try {
    await api.post(`/api/incidents/${id}/${vote}`);
    toast(vote === "confirm" ? "Confirmed 👍" : "Marked gone — thanks");
    refreshIncidents();
  } catch (e) { toast(e.message); }
};

async function refreshIncidents() {
  if (!state.pos || !map || !state.user) return;
  try {
    const items = await api.get(
      `/api/incidents/near?lat=${state.pos.lat}&lng=${state.pos.lng}&radius_m=15000`);
    state.incidents = items;
    incidentMarkers.forEach((m) => m.remove());
    incidentMarkers = [];
    items.forEach((it) => {
      const el = document.createElement("div");
      el.className = "incident-marker";
      el.textContent = it.emoji;
      const mk = new maplibregl.Marker({ element: el })
        .setLngLat([it.lng, it.lat])
        .setPopup(new maplibregl.Popup({ offset: 20 }).setHTML(incidentPopupHtml(it)))
        .addTo(map);
      incidentMarkers.push(mk);
    });
  } catch (e) { /* incidents are best-effort; stay silent */ }
}
function startIncidentFeed() {
  if (incidentTimer) return;
  refreshIncidents();
  incidentTimer = setInterval(refreshIncidents, 60000);
}

function bearingTo(aLat, aLng, bLat, bLng) {
  const t = Math.PI / 180;
  const dLo = (bLng - aLng) * t;
  const y = Math.sin(dLo) * Math.cos(bLat * t);
  const x = Math.cos(aLat * t) * Math.sin(bLat * t) -
            Math.sin(aLat * t) * Math.cos(bLat * t) * Math.cos(dLo);
  return ((Math.atan2(y, x) / t) + 360) % 360;
}
let incidentBannerTimer = null;
function showIncidentBanner(it) {
  $("incident-alert-emoji").textContent = it.emoji;
  $("incident-alert-text").textContent = `${it.label} ahead`;
  $("incident-alert").hidden = false;
  clearTimeout(incidentBannerTimer);
  incidentBannerTimer = setTimeout(() => ($("incident-alert").hidden = true), 9000);
}
function checkIncidentAlerts() {
  if (!state.incidents.length || !state.pos) return;
  const { lat, lng, bearing } = state.pos;
  for (const it of state.incidents) {
    if (nav.alertedIncidents.has(it.id)) continue;
    if (haversineM(lat, lng, it.lat, it.lng) > it.alert_m) continue;
    if (bearing) { // only warn about what's actually ahead of us
      const brg = bearingTo(lat, lng, it.lat, it.lng);
      let diff = Math.abs(brg - bearing) % 360;
      if (diff > 180) diff = 360 - diff;
      if (diff > 100) continue;
    }
    nav.alertedIncidents.add(it.id);
    speak(it.voice_text);
    showIncidentBanner(it);
    break; // one alert per position update
  }
}

/* ================= search ================= */
let suggestTimer = null;
$("search-dest").addEventListener("input", (e) => {
  clearTimeout(suggestTimer);
  const q = e.target.value.trim();
  $("btn-clear-search").hidden = !q;
  if (q.length < 3) { $("suggest-list").hidden = true; return; }
  suggestTimer = setTimeout(() => runSuggest(q), 350);
});
$("btn-clear-search").onclick = () => {
  $("search-dest").value = ""; $("btn-clear-search").hidden = true;
  $("suggest-list").hidden = true; clearRoute();
};
async function runSuggest(q) {
  try {
    const params = new URLSearchParams({ q });
    if (state.pos) { params.set("lat", state.pos.lat); params.set("lng", state.pos.lng); }
    const items = await api.get("/api/search/suggest?" + params);
    const box = $("suggest-list");
    box.innerHTML = "";
    if (!items.length) {
      box.innerHTML = '<div class="suggest-item"><div class="t">No results</div><div class="a">Try a full address or ZIP</div></div>';
    }
    items.forEach((it) => {
      const d = document.createElement("div");
      d.className = "suggest-item";
      d.innerHTML = `<div class="t"></div><div class="a"></div>`;
      d.querySelector(".t").textContent = it.label;
      d.querySelector(".a").textContent = it.address || it.category || "";
      d.onclick = () => selectDestination(it);
      box.appendChild(d);
    });
    box.hidden = false;
  } catch (e) { toast(e.message); }
}
function selectDestination(it) {
  $("suggest-list").hidden = true;
  if (state.addingStop) {
    // multi-stop mode: append as a waypoint, keep the destination
    state.addingStop = false;
    $("search-dest").value = "";
    $("search-dest").placeholder = "Where to?";
    $("btn-clear-search").hidden = true;
    state.stops.push({ lat: it.lat, lng: it.lng, label: it.label });
    drawStopMarkers();
    calculateRoute();
    return;
  }
  $("search-dest").value = it.label;
  state.dest = { lat: it.lat, lng: it.lng, label: it.label };
  if (destMarker) destMarker.remove();
  destMarker = new maplibregl.Marker({ color: "#f5a623" })
    .setLngLat([it.lng, it.lat]).addTo(map);
  calculateRoute();
}
$("btn-add-stop").onclick = () => {
  if (!state.dest) { toast("Pick a destination first"); return; }
  if (state.stops.length >= 10) { toast("Maximum 10 stops"); return; }
  state.addingStop = true;
  $("search-dest").placeholder = "Search for a stop…";
  $("search-dest").focus();
  toast("Search an address to add it as a stop");
};
function stopAction(i, act) {
  if (act === "rm") state.stops.splice(i, 1);
  else if (act === "up" && i > 0) [state.stops[i - 1], state.stops[i]] = [state.stops[i], state.stops[i - 1]];
  else if (act === "dn" && i < state.stops.length - 1) [state.stops[i + 1], state.stops[i]] = [state.stops[i], state.stops[i + 1]];
  else return;
  drawStopMarkers();
  calculateRoute();
}
let stopMarkers = [];
function drawStopMarkers() {
  stopMarkers.forEach((m) => m.remove());
  stopMarkers = [];
  if (!map) return;
  state.stops.forEach((s, i) => {
    const el = document.createElement("div");
    el.className = "stop-n";
    el.textContent = i + 1;
    stopMarkers.push(new maplibregl.Marker({ element: el })
      .setLngLat([s.lng, s.lat]).addTo(map));
  });
}
function renderStops() {
  const box = $("stop-list");
  box.innerHTML = "";
  if (!state.stops.length && !state.dest) return;
  const o = state.route && state.route.options[state.selOption];
  const legs = (o && o.legs) || [];
  const legText = (i) => legs[i]
    ? `${legs[i].distance_miles} mi · ${fmtDur(legs[i].duration_min)}` : "";
  const org = document.createElement("div");
  org.className = "stop-endpoint";
  org.innerHTML = `<span class="dot">🟢</span><span></span>`;
  org.querySelector("span:nth-child(2)").textContent = "Current location";
  box.appendChild(org);
  state.stops.forEach((s, i) => {
    const d = document.createElement("div");
    d.className = "stop-row";
    d.innerHTML = `<span class="stop-n">${i + 1}</span><span class="stop-l"></span>
      <span class="stop-m">${legText(i)}</span>
      <button data-a="up" title="Move up">↑</button><button data-a="dn" title="Move down">↓</button><button data-a="rm" title="Remove">✕</button>`;
    d.querySelector(".stop-l").textContent = s.label;
    d.querySelectorAll("button").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); stopAction(i, b.dataset.a); }));
    box.appendChild(d);
  });
  if (state.dest) {
    const d = document.createElement("div");
    d.className = "stop-endpoint";
    d.innerHTML = `<span class="dot">🏁</span><span></span><span class="stop-m">${legText(state.stops.length)}</span>`;
    d.querySelector("span:nth-child(2)").textContent = state.dest.label;
    box.appendChild(d);
  }
}
$("btn-parking").onclick = async () => {
  if (!state.pos) { toast("Need a GPS fix first"); return; }
  try {
    const items = await api.get(
      `/api/search/places?q=truck%20stop&lat=${state.pos.lat}&lng=${state.pos.lng}`);
    if (!items.length) { toast("No truck stops found nearby"); return; }
    items.slice(0, 12).forEach((it) => {
      new maplibregl.Marker({ color: "#2f9e44" })
        .setLngLat([it.lng, it.lat])
        .setPopup(new maplibregl.Popup().setText(it.label))
        .addTo(map);
    });
    const first = items[0];
    map.flyTo({ center: [first.lng, first.lat], zoom: 11 });
    toast(items.length + " truck stops marked");
  } catch (e) { toast(e.message); }
};

/* ================= routing ================= */
async function calculateRoute() {
  if (!state.dest) return;
  if (!state.pos) { toast("Waiting for GPS fix…"); return; }
  try {
    toast("Calculating route…");
    const body = {
      origin: { lat: state.pos.lat, lng: state.pos.lng, label: "Current location" },
      destination: { lat: state.dest.lat, lng: state.dest.lng, label: state.dest.label },
      waypoints: state.stops.map((s) => ({ lat: s.lat, lng: s.lng, label: s.label })),
      truck_id: state.activeTruck ? state.activeTruck.id : null,
      alternatives: true, save_trip: false,
    };
    const r = await api.post("/api/routes/calculate", body);
    state.route = r; state.routeId = r.route_id; state.selOption = 0;
    renderRouteOptions();
    drawRoute(r.options[0].shape);
    fitRoute(r.options[0].shape);
    $("route-panel").hidden = false;
  } catch (e) { toast(e.message); }
}
function renderRouteOptions() {
  const box = $("route-options");
  box.innerHTML = "";
  state.route.options.forEach((o, i) => {
    const d = document.createElement("div");
    d.className = "route-opt" + (i === state.selOption ? " sel" : "");
    const safe = state.route.truck_safe ? '<div class="s">✓ truck-safe</div>' : '<div class="s" style="color:#f5a623">not truck-checked</div>';
    d.innerHTML = `<div class="l">${o.label}</div><div class="d">${o.distance_miles} mi · ${fmtDur(o.duration_min)}</div>${safe}`;
    d.onclick = () => { state.selOption = i; renderRouteOptions(); drawRoute(o.shape); };
    box.appendChild(d);
  });
  const sel = state.route.options[state.selOption];
  const arrive = new Date(Date.now() + sel.duration_min * 60000);
  $("sheet-big").textContent = fmtDur(sel.duration_min);
  $("sheet-sub").textContent =
    `${sel.distance_miles} mi · arrive ${arrive.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` +
    ` · ${state.route.truck_safe ? "truck-safe route" : "not truck-checked"}` +
    (state.route.load && state.route.load.special && !state.route.load.enforced
      ? " · ⚠ load profile NOT enforced by this engine" : "");
  renderStops();
  refreshRouteExtras(sel);
}
function drawRoute(shape) {
  if (!map || !map.getSource("route")) return;
  map.getSource("route").setData({ type: "Feature", geometry: shape, properties: {} });
}
function fitRoute(shape) {
  const c = shape.coordinates;
  if (!c.length) return;
  const b = new maplibregl.LngLatBounds(c[0], c[0]);
  c.forEach((p) => b.extend(p));
  map.fitBounds(b, { padding: 60 });
}

/* ============ along-route extras: reported slowdowns + rest stops ============
   Driver-reported slowdowns come ONLY from this app's own reports (POST
   /api/incidents/along) and stay labeled as such. LIVE traffic is a
   separate TomTom feed (POST /api/traffic/along) rendered in its own
   labeled section below — the two sources are never blended.
   Rest stops come from the map-data places search along the route. */
let extrasSeq = 0;
function refreshRouteExtras(option) {
  const seq = ++extrasSeq;
  fetchRouteIncidents(option, seq);
  fetchRouteTraffic(option, seq);
  fetchRouteRests(option, seq);
}
function cumMilesArr(coords) {
  const cum = [0];
  for (let i = 1; i < coords.length; i++)
    cum.push(cum[i - 1] + haversineM(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0]) / 1609.344);
  return cum;
}
function nearestCoordIdx(coords, lat, lng) {
  let bi = 0, bd = Infinity;
  for (let i = 0; i < coords.length; i += 2) {
    const d = haversineM(lat, lng, coords[i][1], coords[i][0]);
    if (d < bd) { bd = d; bi = i; }
  }
  const lo = Math.max(0, bi - 2), hi = Math.min(coords.length - 1, bi + 2);
  for (let i = lo; i <= hi; i++) {
    const d = haversineM(lat, lng, coords[i][1], coords[i][0]);
    if (d < bd) { bd = d; bi = i; }
  }
  return bi;
}
function subShapeAround(coords, idx, radiusM) {
  let lo = idx, acc = 0;
  while (lo > 0 && acc < radiusM) {
    acc += haversineM(coords[lo][1], coords[lo][0], coords[lo - 1][1], coords[lo - 1][0]); lo--;
  }
  let hi = idx; acc = 0;
  while (hi < coords.length - 1 && acc < radiusM) {
    acc += haversineM(coords[hi][1], coords[hi][0], coords[hi + 1][1], coords[hi + 1][0]); hi++;
  }
  return coords.slice(lo, hi + 1);
}
async function fetchRouteIncidents(option, seq) {
  const pts = downsamplePts(option.shape.coordinates);
  let items = [];
  try {
    items = await api.post("/api/incidents/along", { points: pts, corridor_m: 600 }) || [];
  } catch (e) { items = []; }
  if (seq !== extrasSeq || !state.route) return; // a newer route superseded us
  state.routeIncidents = items;
  drawSlowdowns(option);
  renderAhead();
}
function drawSlowdowns(option) {
  if (!map || !map.getSource("route-slow")) return;
  const coords = option.shape.coordinates;
  const feats = [];
  (state.routeIncidents || []).filter((it) => it.slows_traffic).forEach((it) => {
    const slice = subShapeAround(coords, nearestCoordIdx(coords, it.lat, it.lng), 320);
    if (slice.length > 1)
      feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: slice }, properties: {} });
  });
  map.getSource("route-slow").setData({ type: "FeatureCollection", features: feats });
}
function renderAhead() {
  const box = $("sheet-ahead");
  const items = state.routeIncidents || [];
  const slows = items.filter((i) => i.slows_traffic).length;
  const chip = $("sheet-slow-chip");
  chip.hidden = !slows;
  if (slows)
    chip.textContent = `🐢 ${slows} driver-reported slowdown${slows > 1 ? "s" : ""} ahead`;
  box.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = "Ahead on your route";
  const note = document.createElement("p"); note.className = "sheet-note";
  note.textContent = "From TruckRoute driver reports — not a live traffic feed.";
  box.appendChild(h); box.appendChild(note);
  if (!items.length) {
    const d = document.createElement("div");
    d.className = "ahead-row";
    d.innerHTML = `<span class="e">✅</span><span class="l">No driver-reported problems</span>`;
    box.appendChild(d);
  }
  items.forEach((it) => {
    const d = document.createElement("div");
    d.className = "ahead-row" + (it.slows_traffic ? " slow" : "");
    const age = it.age_min < 1 ? "just now" : `${it.age_min} min ago`;
    d.innerHTML = `<span class="e"></span><span class="l"></span><span class="m"></span>`;
    d.querySelector(".e").textContent = it.emoji;
    d.querySelector(".l").textContent = it.label;
    d.querySelector(".m").textContent = `${it.miles_along} mi ahead · ${age}`;
    box.appendChild(d);
  });
  box.hidden = false;
}
/* ============ live traffic (TomTom) — separate from driver reports ============
   Dormant unless the server has a TomTom key: the endpoint answers
   enabled=false and this section never renders. When live data comes
   back it gets its OWN labeled section and red route segments (driver
   reports are orange) so the two sources can't be confused. */
function downsamplePts(coords) {
  const step = Math.max(1, Math.floor(coords.length / 180));
  const pts = coords.filter((_, i) => i % step === 0);
  if (pts.length && pts[pts.length - 1] !== coords[coords.length - 1])
    pts.push(coords[coords.length - 1]);
  return pts;
}
async function fetchRouteTraffic(option, seq) {
  let resp = null;
  try {
    resp = await api.post("/api/traffic/along",
      { points: downsamplePts(option.shape.coordinates), corridor_m: 600 });
  } catch (e) { resp = null; }
  if (seq !== extrasSeq || !state.route) return; // a newer route superseded us
  state.routeTraffic = resp;
  drawLiveSlowdowns(option);
  renderLiveTraffic();
}
function drawLiveSlowdowns(option) {
  if (!map || !map.getSource("route-live-slow")) return;
  const coords = option.shape.coordinates;
  const feats = [];
  const t = state.routeTraffic;
  if (t && t.enabled) {
    (t.slowdowns || []).forEach((s) => {
      const slice = subShapeAround(coords, nearestCoordIdx(coords, s.lat, s.lng), 420);
      if (slice.length > 1)
        feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: slice }, properties: {} });
    });
  }
  map.getSource("route-live-slow").setData({ type: "FeatureCollection", features: feats });
}
function renderLiveTraffic() {
  const box = $("sheet-live");
  const t = state.routeTraffic;
  // No key on the server (or request failed): show nothing at all —
  // exactly the pre-TomTom behavior.
  if (!t || !t.enabled) { box.hidden = true; box.innerHTML = ""; return; }
  box.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = "Live traffic — TomTom";
  const note = document.createElement("p"); note.className = "sheet-note";
  note.textContent = "Live data from TomTom — separate from the driver reports above.";
  box.appendChild(h); box.appendChild(note);
  if (!t.available) {
    const d = document.createElement("div");
    d.className = "ahead-row live";
    d.innerHTML = `<span class="e">📡</span><span class="l"></span>`;
    d.querySelector(".l").textContent = t.message || "Live traffic unavailable right now.";
    box.appendChild(d);
    box.hidden = false;
    return;
  }
  if (t.message) {
    const n = document.createElement("p"); n.className = "sheet-note";
    n.textContent = t.message;
    box.appendChild(n);
  }
  const inc = t.incidents || [], slow = t.slowdowns || [];
  if (!inc.length && !slow.length) {
    const d = document.createElement("div");
    d.className = "ahead-row live";
    d.innerHTML = `<span class="e">✅</span><span class="l">No live incidents or slowdowns</span>`;
    box.appendChild(d);
  }
  // Merge by miles_along so the list reads in driving order.
  const rows = [
    ...inc.map((i) => ({ mi: i.miles_along, inc: i })),
    ...slow.map((s) => ({ mi: s.miles_along, slow: s })),
  ].sort((a, b) => a.mi - b.mi);
  rows.forEach((r) => {
    const d = document.createElement("div");
    d.className = "ahead-row live";
    if (r.inc) {
      const it = r.inc;
      d.innerHTML = `<span class="e"></span><span class="l"><span class="t"></span><span class="d"></span></span><span class="m"></span>`;
      d.querySelector(".e").textContent = it.emoji;
      d.querySelector(".t").textContent = it.label;
      const sub = [it.description !== it.label ? it.description : "", it.road].filter(Boolean).join(" · ");
      d.querySelector(".d").textContent = sub;
      d.querySelector(".m").textContent =
        `${it.miles_along} mi ahead` + (it.delay_min ? ` · ~${Math.round(it.delay_min)} min delay` : "");
    } else {
      const s = r.slow;
      d.innerHTML = `<span class="e">🔴</span><span class="l"><span class="t">Slow traffic</span><span class="d"></span></span><span class="m"></span>`;
      d.querySelector(".d").textContent = s.road_closure
        ? "Road closed on this stretch"
        : `Moving at ${s.current_mph} mph (free flow ${s.free_flow_mph} mph)`;
      d.querySelector(".m").textContent = `${s.miles_along} mi ahead`;
    }
    box.appendChild(d);
  });
  box.hidden = false;
}
async function fetchRouteRests(option, seq) {
  const box = $("sheet-rests");
  box.hidden = true; box.innerHTML = "";
  state.routeRests = [];
  const totalMi = option.distance_miles, coords = option.shape.coordinates;
  if (totalMi < 60 || coords.length < 2) return;
  const cum = cumMilesArr(coords);
  const durH = option.duration_min / 60;
  const avgMph = durH > 0 ? totalMi / durH : 55;
  const targets = [];
  const breakH = state.prefs && state.prefs.break_interval_hours;
  state.restsBreakH = breakH && breakH > 0 ? breakH : null;
  if (breakH && breakH > 0) {
    for (let m = breakH * avgMph; m < totalMi - 25 && targets.length < 3; m += breakH * avgMph)
      targets.push(m);
  }
  if (!targets.length)
    (totalMi > 150 ? [0.4, 0.75] : [0.55]).forEach((f) => targets.push(totalMi * f));
  const found = [], seen = new Set();
  for (const t of targets) {
    let ci = cum.findIndex((v) => v >= t);
    if (ci < 0) ci = coords.length - 1;
    const pt = { lat: coords[ci][1], lng: coords[ci][0] };
    let items = [];
    try {
      // One call per target: the backend POI search unions rest areas,
      // services, fuel and HGV parking regardless of the query wording.
      items = await api.get(`/api/search/places?q=${encodeURIComponent("truck stop")}&lat=${pt.lat}&lng=${pt.lng}`);
    } catch (e) { items = []; }
    let best = null, bd = 30 * 1609.344;
    (items || []).forEach((it) => {
      const d = haversineM(pt.lat, pt.lng, it.lat, it.lng);
      if (d < bd) { bd = d; best = it; }
    });
    if (best) {
      const key = best.label + "|" + best.lat.toFixed(3) + "|" + best.lng.toFixed(3);
      if (!seen.has(key)) {
        seen.add(key);
        found.push({ label: best.label, lat: best.lat, lng: best.lng,
          miles: Math.round(cum[nearestCoordIdx(coords, best.lat, best.lng)]) });
      }
    }
  }
  if (seq !== extrasSeq || !state.route) return;
  found.sort((a, b) => a.miles - b.miles);
  state.routeRests = found;
  renderRests();
}
function renderRests() {
  const box = $("sheet-rests");
  const rests = state.routeRests || [];
  box.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = "Rest & parking on this route";
  const note = document.createElement("p"); note.className = "sheet-note";
  note.textContent = state.restsBreakH
    ? `Timed around your break every ${state.restsBreakH}h (Account → Driving preferences), so a stop is there before your clock forces one. From map data (OpenStreetMap) — lots fill up, call ahead when you can. Planning estimates only, not an ELD.`
    : "From map data (OpenStreetMap) — lots fill up, call ahead when you can. Tip: set a break interval in Account → Driving preferences and these get timed to your breaks. Planning estimates only, not an ELD.";
  box.appendChild(h); box.appendChild(note);
  if (!rests.length) {
    const d = document.createElement("div");
    d.className = "rest-row";
    d.innerHTML = `<span class="e">🅿️</span><span class="l">None found in map data near this route</span>`;
    box.appendChild(d);
  }
  rests.forEach((r) => {
    const d = document.createElement("div");
    d.className = "rest-row";
    d.innerHTML = `<span class="e">🅿️</span><span class="l"></span><span class="m"></span><button>Add stop</button>`;
    d.querySelector(".l").textContent = r.label;
    d.querySelector(".m").textContent = `~${r.miles} mi in`;
    d.querySelector("button").onclick = () => {
      if (state.stops.length >= 10) { toast("Maximum 10 stops"); return; }
      state.stops.push({ lat: r.lat, lng: r.lng, label: r.label });
      drawStopMarkers();
      calculateRoute();
      toast("Rest stop added to your route");
    };
    box.appendChild(d);
  });
  box.hidden = false;
}
function clearRoute() {
  state.route = null; state.dest = null; state.stops = []; state.addingStop = false;
  state.routeIncidents = []; state.routeTraffic = null; state.routeRests = []; extrasSeq++;
  $("search-dest").placeholder = "Where to?";
  stopMarkers.forEach((m) => m.remove()); stopMarkers = [];
  if (map && map.getSource("route"))
    map.getSource("route").setData({ type: "FeatureCollection", features: [] });
  if (map && map.getSource("route-slow"))
    map.getSource("route-slow").setData({ type: "FeatureCollection", features: [] });
  if (map && map.getSource("route-live-slow"))
    map.getSource("route-live-slow").setData({ type: "FeatureCollection", features: [] });
  if (map && map.getSource("route-done"))
    map.getSource("route-done").setData({ type: "FeatureCollection", features: [] });
  if (destMarker) { destMarker.remove(); destMarker = null; }
  $("sheet-ahead").hidden = true;
  $("sheet-live").hidden = true;
  $("sheet-rests").hidden = true;
  $("sheet-slow-chip").hidden = true;
  $("route-panel").hidden = true;
}
$("btn-cancel-route").onclick = () => { clearRoute(); $("search-dest").value = ""; $("btn-clear-search").hidden = true; };
function fmtDur(min) {
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return h ? `${h} hr ${m} min` : `${m} min`;
}

/* ---- trip summary ---- */
$("btn-trip-summary").onclick = async () => {
  if (!state.route) return;
  try {
    const body = {
      origin: { lat: state.pos.lat, lng: state.pos.lng, label: "Current location" },
      destination: { lat: state.dest.lat, lng: state.dest.lng, label: state.dest.label },
      waypoints: state.stops.map((s) => ({ lat: s.lat, lng: s.lng, label: s.label })),
      truck_id: state.activeTruck ? state.activeTruck.id : null,
      alternatives: true, save_trip: true,
      trip_name: state.dest.label,
    };
    const r = await api.post("/api/routes/calculate", body);
    state.route = r; state.routeId = r.route_id; state.selOption = 0;
    renderRouteOptions(); drawRoute(r.options[0].shape);
    openSummary();
  } catch (e) { toast(e.message); }
};
function openSummary() {
  const r = state.route, o = r.options[state.selOption];
  const tabs = $("summary-route-tabs"); tabs.innerHTML = "";
  r.options.forEach((op, i) => {
    const b = document.createElement("button");
    b.textContent = `${op.label} · ${op.distance_miles} mi`;
    b.className = i === state.selOption ? "sel" : "";
    b.onclick = () => { state.selOption = i; renderRouteOptions(); drawRoute(op.shape); openSummary(); };
    tabs.appendChild(b);
  });
  const miles = o.distance_miles, fuel = o.est_fuel_gal;
  const range = r.fuel_range_miles;
  const needFuel = range != null && miles > range * 0.85;
  $("summary-list").innerHTML = `
    <dt>Total distance</dt><dd>${miles} mi</dd>
    <dt>Drive time</dt><dd>${fmtDur(o.duration_min)}</dd>
    <dt>Est. fuel</dt><dd>${fuel != null ? fuel + " gal" : "—"}</dd>
    <dt>Your range</dt><dd>${range != null ? range + " mi" : "—"}</dd>
    <dt>Routing</dt><dd>${r.truck_safe ? "✓ truck-safe" : "not truck-checked"}</dd>
    ${r.load ? `<dt>Load</dt><dd style="font-size:15px">${escapeHtml(r.load.summary)}<br>${r.load.enforced
      ? '<span style="color:#2f9e44">✓ restrictions enforced by routing provider</span>'
      : '<span style="color:#f5a623">recorded — NOT enforced by this routing engine</span>'}</dd>` : ""}
    <dt>Truck</dt><dd style="font-size:15px">${r.truck_label || "—"}</dd>`;
  const w = $("summary-warnings"); w.innerHTML = "";
  r.warnings.forEach((msg) => {
    const d = document.createElement("div");
    d.className = "warn" + (r.truck_safe && !/violat/i.test(msg) ? " truck-ok" : "");
    d.textContent = msg; w.appendChild(d);
  });
  if (needFuel) {
    const d = document.createElement("div");
    d.className = "warn"; d.textContent = `Heads up: this trip (${miles} mi) is close to your fuel range (${range} mi). Plan a fuel stop.`;
    w.appendChild(d);
  }
  $("summary-hos").innerHTML = r.hos && r.hos.summary
    ? `<div class="warn truck-ok">⏱ ${r.hos.summary}</div>` : "";
  $("modal-summary").hidden = false;
}
$("btn-close-summary").onclick = () => ($("modal-summary").hidden = true);

/* ================= turn-by-turn navigation ================= */
const nav = { maneuvers: [], coords: [], idx: 0, announced: {}, offCount: 0,
  lastFix: 0, startTime: 0,
  driveMs: 0, lastTick: 0, breakSpokenCycle: -1, // HOS break planning (estimates)
  alertedIncidents: new Set(), lastIncidentRefresh: 0, wpHit: new Set() };

$("btn-start-nav").onclick = async () => {
  const o = state.route.options[state.selOption];
  $("modal-summary").hidden = true;
  nav.maneuvers = o.maneuvers; nav.coords = o.shape.coordinates;
  nav.idx = 0; nav.announced = {}; nav.offCount = 0;
  nav.alertedIncidents = new Set(); nav.wpHit = new Set(); nav.lastIncidentRefresh = Date.now();
  nav.driveMs = 0; nav.lastTick = Date.now(); nav.breakSpokenCycle = -1;
  state.navigating = true;
  followMode = true; lastCamAt = 0; // nav always starts in follow mode
  if (NATIVE) { try { window.TruckRoute.startNavService(); } catch (e) {} }
  try {
    const s = await api.post("/api/nav/start", { route_id: state.routeId });
    state.sessionId = s.id;
  } catch (e) {}
  $("route-panel").hidden = true;
  $("search-bar").style.display = "none";
  $("bottom-nav").style.display = "none";
  $("nav-hud").hidden = false;
  nav.startTime = Date.now();
  speak("Navigation started. " + (nav.maneuvers[0] ? nav.maneuvers[0].instruction : ""));
  toast("Drive safe — keep your eyes on the road");
};

$("btn-end-nav").onclick = async () => {
  state.navigating = false;
  followMode = true; // restore gentle north-up follow
  if (map) map.easeTo({ bearing: 0, pitch: 0, duration: 500 });
  if (NATIVE) { try { window.TruckRoute.stopNavService(); } catch (e) {} }
  try { if (state.sessionId) await api.post(`/api/nav/${state.sessionId}/end`); } catch (e) {}
  state.sessionId = null;
  $("nav-hud").hidden = true;
  $("search-bar").style.display = "";
  $("bottom-nav").style.display = "";
  $("route-panel").hidden = false;
  if (map && map.getSource("route-done"))
    map.getSource("route-done").setData({ type: "FeatureCollection", features: [] });
};

function haversineM(aLat, aLng, bLat, bLng) {
  const R = 6371000, t = Math.PI / 180;
  const dLa = (bLat - aLat) * t, dLo = (bLng - aLng) * t;
  const s = Math.sin(dLa / 2) ** 2 + Math.cos(aLat * t) * Math.cos(bLat * t) * Math.sin(dLo / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}
function nearestOnRoute(lat, lng) {
  // coarse scan (every 4th point), then refine — cheap and good enough
  let bi = 0, bd = Infinity;
  for (let i = 0; i < nav.coords.length; i += 4) {
    const d = haversineM(lat, lng, nav.coords[i][1], nav.coords[i][0]);
    if (d < bd) { bd = d; bi = i; }
  }
  const lo = Math.max(0, bi - 4), hi = Math.min(nav.coords.length - 1, bi + 4);
  for (let i = lo; i <= hi; i++) {
    const d = haversineM(lat, lng, nav.coords[i][1], nav.coords[i][0]);
    if (d < bd) { bd = d; bi = i; }
  }
  return { idx: bi, dist: bd };
}
function remainingMiles(fromIdx) {
  let m = 0;
  for (let i = fromIdx; i < nav.coords.length - 1; i++)
    m += haversineM(nav.coords[i][1], nav.coords[i][0], nav.coords[i + 1][1], nav.coords[i + 1][0]);
  return m / 1609.344;
}
async function navUpdate() {
  if (!state.navigating || !state.pos || !nav.coords.length) return;
  const now = Date.now();
  if (now - nav.lastFix < 1000) return; // fixes now stream at ~1Hz
  nav.lastFix = now;
  const { lat, lng } = state.pos;
  const near = nearestOnRoute(lat, lng);

  // HOS break planning (estimate, not an ELD): accumulate wheel-turning
  // time and count down to the driver's own break interval.
  const tickNow = Date.now();
  const dtMs = nav.lastTick ? Math.min(tickNow - nav.lastTick, 10000) : 0;
  nav.lastTick = tickNow;
  if ((state.pos.speed || 0) * 2.23694 > 3) nav.driveMs += dtMs;
  const breakH = state.prefs && state.prefs.break_interval_hours;
  const breakChip = $("nav-break");
  if (breakH && breakH > 0) {
    const breakMs = breakH * 3600000;
    const cycle = Math.floor(nav.driveMs / breakMs);
    const remainMs = breakMs - (nav.driveMs % breakMs);
    breakChip.hidden = false;
    breakChip.textContent =
      `⏸ Break in ${fmtDur(remainMs / 60000)} · driving ${fmtDur(nav.driveMs / 60000)} (est.)`;
    if (remainMs < 5 * 60000 && nav.breakSpokenCycle !== cycle) {
      nav.breakSpokenCycle = cycle;
      speak(`You have been driving about ${breakH} hours. A break is coming up. Rest stops for this route are in the trip summary.`);
    }
  } else {
    breakChip.hidden = true;
  }

  // waypoint arrival check (multi-stop)
  state.stops.forEach((s, i) => {
    if (nav.wpHit.has(i)) return;
    if (haversineM(lat, lng, s.lat, s.lng) < 150) {
      nav.wpHit.add(i);
      const done = nav.wpHit.size, total = state.stops.length;
      speak(done < total
        ? `Arrived at stop ${i + 1} of ${total}.`
        : `Arrived at stop ${i + 1} of ${total}. Continuing to your destination.`);
      toast(`✓ Stop ${i + 1} of ${total}`);
    }
  });

  // arrival check
  const last = nav.coords[nav.coords.length - 1];
  if (haversineM(lat, lng, last[1], last[0]) < 60) {
    speak("You have arrived at your destination.");
    try { if (state.sessionId) await api.post(`/api/nav/${state.sessionId}/event`, { event: "arrived" }); } catch (e) {}
    $("btn-end-nav").click();
    return;
  }

  // off-route detection
  if (near.dist > 120) {
    nav.offCount++;
    if (nav.offCount >= 2) {
      nav.offCount = 0;
      if (state.prefs && state.prefs.auto_recalculate !== false) {
        speak("Recalculating route.");
        try { if (state.sessionId) await api.post(`/api/nav/${state.sessionId}/event`, { event: "recalculated" }); } catch (e) {}
        state.dest = { ...state.dest }; // keep
        const keepDest = state.dest;
        await calculateRoute(); // recalculates from current GPS
        state.dest = keepDest;
        const o = state.route && state.route.options[0];
        if (o) { nav.maneuvers = o.maneuvers; nav.coords = o.shape.coordinates; nav.idx = 0; nav.announced = {}; }
        return;
      } else { toast("Off route — auto recalc is off"); }
    }
  } else nav.offCount = 0;

  // advance past maneuvers we've reached
  while (nav.idx < nav.maneuvers.length) {
    const m = nav.maneuvers[nav.idx];
    if (haversineM(lat, lng, m.lat, m.lng) < 45) nav.idx++;
    else break;
  }
  const m = nav.maneuvers[nav.idx];
  if (!m) return;
  const dTo = haversineM(lat, lng, m.lat, m.lng);

  // announce: once at ~500m, again at ~120m
  if (dTo < 500 && !nav.announced[m.index + "-far"]) {
    nav.announced[m.index + "-far"] = 1;
    speak(`In ${(dTo / 1609.344).toFixed(1)} miles, ${m.instruction}`);
  } else if (dTo < 120 && !nav.announced[m.index + "-now"]) {
    nav.announced[m.index + "-now"] = 1;
    speak(m.lane_hint ? `${m.instruction} ${m.lane_hint}.` : m.instruction);
  }

  // HUD
  $("nav-instruction").textContent = m.instruction;
  const laneEl = $("nav-lane");
  laneEl.textContent = m.lane_hint ? `🛣 ${m.lane_hint}` : "";
  laneEl.hidden = !m.lane_hint;
  $("nav-dist").textContent = dTo < 1609 ? `${Math.round(dTo * 3.281)} ft` : `${(dTo / 1609.344).toFixed(1)} mi`;
  const remMi = remainingMiles(near.idx);
  $("nav-remain").textContent = remMi.toFixed(0);
  const mph = (state.pos.speed || 0) * 2.23694;
  $("nav-speed").textContent = state.pos.speed != null ? Math.round(mph) : "—";
  $("nav-speed-big").textContent = state.pos.speed != null ? Math.round(mph) : "0";
  const elapsedMin = (Date.now() - nav.startTime) / 60000;
  const o0 = state.route.options[state.selOption];
  const leftMin = Math.max(0, o0.duration_min - elapsedMin);
  $("nav-time").textContent = fmtDur(leftMin);
  const eta = new Date(Date.now() + leftMin * 60000);
  $("nav-eta").textContent = eta.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

  // traveled-portion line
  if (map && map.getSource("route-done")) {
    map.getSource("route-done").setData({
      type: "Feature", geometry: { type: "LineString", coordinates: nav.coords.slice(0, near.idx + 1) }, properties: {},
    });
  }
  // camera follow is handled by the puck render loop (bearing + look-ahead)

  // incident feed refresh + proximity voice alerts
  if (now - nav.lastIncidentRefresh > 60000) {
    nav.lastIncidentRefresh = now;
    refreshIncidents();
  }
  checkIncidentAlerts();
}


/* ================= trucks ================= */
const LOAD_TYPE_LABELS = {
  general: "General freight", flatbed: "Flatbed / open deck", tanker: "Tanker",
  oversize: "Oversize / overweight (permit)", hazmat: "Hazmat (placarded)",
};
let editingTruck = null;
async function loadTrucks() {
  try {
    state.trucks = await api.get("/api/trucks");
    state.activeTruck = state.trucks.find((t) => t.is_active) || null;
    const box = $("truck-list"); box.innerHTML = "";
    if (!state.trucks.length)
      box.innerHTML = '<div class="card"><div class="s">No trucks yet. Add your rig so routes use its size and weight.</div></div>';
    state.trucks.forEach((t) => {
      const d = document.createElement("div");
      d.className = "card";
      d.innerHTML = `<div class="t">${escapeHtml(t.nickname || (t.make + " " + t.model).trim() || "Truck")}${t.is_active ? '<span class="badge">ACTIVE</span>' : ""}</div>
        <div class="s">${t.height_ft}'${t.height_in}" · ${(t.weight_lbs / 1000).toFixed(0)}k lbs · ${t.length_ft}' long · ${t.axle_count} axles</div>
        <div class="s">🚛 Load: ${escapeHtml(LOAD_TYPE_LABELS[t.load_type] || "General freight")}${t.hazmat_class ? ` · Hazmat class ${t.hazmat_class}` : ""}${t.hazmat ? ` · ${escapeHtml(t.hazmat)}` : ""}</div>
        <div class="s">⛽ ${t.fuel_capacity_gal} gal · ${t.fuel_level_pct}% · ${t.avg_mpg} mpg → ~${t.fuel_range_miles} mi range</div>
        <div class="row">
          ${t.is_active ? "" : '<button data-act="active" class="primary">Set active</button>'}
          <button data-act="edit">Edit</button>
          <button data-act="del" class="danger">Delete</button>
        </div>`;
      d.querySelectorAll("button").forEach((b) => (b.onclick = () => truckAction(t, b.dataset.act)));
      box.appendChild(d);
    });
  } catch (e) { toast(e.message); }
}
async function truckAction(t, act) {
  try {
    if (act === "active") await api.put("/api/trucks/active", { truck_id: t.id });
    if (act === "del" && confirm(`Delete "${t.nickname || "this truck"}"?`)) await api.del(`/api/trucks/${t.id}`);
    if (act === "edit") { editingTruck = t; fillTruckForm(t); $("truck-form-card").hidden = false; return; }
    loadTrucks();
  } catch (e) { toast(e.message); }
}
$("btn-add-truck").onclick = () => { editingTruck = null; fillTruckForm(null); $("truck-form-card").hidden = false; };
$("btn-cancel-truck").onclick = () => ($("truck-form-card").hidden = true);
function fillTruckForm(t) {
  $("truck-form-title").textContent = t ? "Edit truck" : "Add truck";
  const v = (id, val) => ($(id).value = val);
  v("t-nickname", t?.nickname || ""); v("t-make", t?.make || ""); v("t-model", t?.model || "");
  v("t-year", t?.year || ""); v("t-hft", t?.height_ft ?? 13); v("t-hin", t?.height_in ?? 6);
  v("t-win", t?.width_in ?? 102); v("t-lft", t?.length_ft ?? 72); v("t-weight", t?.weight_lbs ?? 80000);
  v("t-tlft", t?.trailer_length_ft || ""); v("t-axles", t?.axle_count ?? 5);
  v("t-cap", t?.fuel_capacity_gal ?? 240); v("t-level", t?.fuel_level_pct ?? 100);
  v("t-mpg", t?.avg_mpg ?? 7.2); v("t-fueltype", t?.fuel_type || "diesel"); v("t-hazmat", t?.hazmat || "");
  v("t-loadtype", t?.load_type || "general"); v("t-hazmatclass", t?.hazmat_class || "");
  $("truck-form-error").textContent = "";
}
$("btn-save-truck").onclick = async () => {
  const num = (id, dflt) => { const v = parseFloat($(id).value); return isNaN(v) ? dflt : v; };
  const body = {
    nickname: $("t-nickname").value.trim(), make: $("t-make").value.trim(), model: $("t-model").value.trim(),
    year: $("t-year").value ? parseInt($("t-year").value) : null,
    height_ft: num("t-hft", 13), height_in: num("t-hin", 6), width_in: num("t-win", 102),
    length_ft: num("t-lft", 72), weight_lbs: num("t-weight", 80000),
    trailer_length_ft: $("t-tlft").value ? parseFloat($("t-tlft").value) : null,
    axle_count: num("t-axles", 5), fuel_capacity_gal: num("t-cap", 240),
    fuel_level_pct: num("t-level", 100), avg_mpg: num("t-mpg", 7.2),
    fuel_type: $("t-fueltype").value.trim() || "diesel",
    hazmat: $("t-hazmat").value.trim() || null,
    load_type: $("t-loadtype").value,
    hazmat_class: $("t-hazmatclass").value || null,
  };
  try {
    if (editingTruck) await api.put(`/api/trucks/${editingTruck.id}`, body);
    else await api.post("/api/trucks", body);
    $("truck-form-card").hidden = true;
    loadTrucks();
    toast("Truck saved");
  } catch (e) { $("truck-form-error").textContent = e.message; }
};

/* ================= saved places ================= */
let lastSearchResult = null;
async function loadSaved() {
  try {
    const items = await api.get("/api/saved-locations");
    const box = $("saved-list"); box.innerHTML = "";
    if (!items.length) box.innerHTML = '<div class="card"><div class="s">Nothing saved yet. Search a destination on the map, then tap "+ Add current search".</div></div>';
    items.forEach((s) => {
      const d = document.createElement("div");
      d.className = "card";
      d.innerHTML = `<div class="t">${escapeHtml(s.label)}${s.is_favorite ? '<span class="badge">★</span>' : ""}</div>
        <div class="s">${escapeHtml(s.address || "")}</div>
        <div class="row"><button data-act="go" class="primary">Navigate</button>
        <button data-act="fav">${s.is_favorite ? "Unfavorite" : "Favorite"}</button>
        <button data-act="del" class="danger">Delete</button></div>`;
      d.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
        try {
          if (b.dataset.act === "go") {
            showScreen("map"); initMapOnce();
            selectDestination({ lat: s.lat, lng: s.lng, label: s.label });
          } else if (b.dataset.act === "fav") {
            await api.put(`/api/saved-locations/${s.id}`, { ...s, is_favorite: !s.is_favorite });
            loadSaved();
          } else if (b.dataset.act === "del" && confirm("Delete this saved place?")) {
            await api.del(`/api/saved-locations/${s.id}`); loadSaved();
          }
        } catch (e) { toast(e.message); }
      }));
      box.appendChild(d);
    });
  } catch (e) { toast(e.message); }
}
$("btn-add-saved").onclick = async () => {
  if (!state.dest) { toast("Search a destination on the map first"); return; }
  const label = prompt("Name this place:", state.dest.label || "");
  if (!label) return;
  try {
    await api.post("/api/saved-locations", { label, address: state.dest.label, lat: state.dest.lat, lng: state.dest.lng });
    toast("Saved"); loadSaved();
  } catch (e) { toast(e.message); }
};

/* ================= trips ================= */
async function loadTrips() {
  try {
    const trips = await api.get("/api/trips");
    const box = $("trip-list"); box.innerHTML = "";
    if (!trips.length) box.innerHTML = '<div class="card"><div class="s">No saved trips yet. Calculate a route and tap "Review trip" to save it.</div></div>';
    trips.forEach((t) => {
      const d = document.createElement("div");
      d.className = "card";
      d.innerHTML = `<div class="t">${escapeHtml(t.name || "Trip")}</div>
        <div class="s">${escapeHtml(t.origin_label || "")} → ${escapeHtml(t.dest_label || "")}</div>
        <div class="s">${t.total_miles != null ? t.total_miles.toFixed(0) + " mi · " : ""}${t.est_drive_min != null ? fmtDur(t.est_drive_min) : ""}${t.stops.length ? ` · ${t.stops.length} stop(s)` : ""}</div>
        <div class="row"><button data-act="load" class="primary">Open</button>
        <button data-act="del" class="danger">Delete</button></div>`;
      d.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
        try {
          if (b.dataset.act === "load") {
            showScreen("map"); initMapOnce();
            state.dest = { lat: t.dest_lat, lng: t.dest_lng, label: t.dest_label };
            $("search-dest").value = t.dest_label || "";
            if (destMarker) destMarker.remove();
            destMarker = new maplibregl.Marker({ color: "#f5a623" }).setLngLat([t.dest_lng, t.dest_lat]).addTo(map);
            await calculateRoute();
          } else if (b.dataset.act === "del" && confirm("Delete this trip?")) {
            await api.del(`/api/trips/${t.id}`); loadTrips();
          }
        } catch (e) { toast(e.message); }
      }));
      box.appendChild(d);
    });
  } catch (e) { toast(e.message); }
}

/* ================= account ================= */
async function loadPrefs() {
  try { state.prefs = await api.get("/api/preferences"); } catch (e) {}
}
async function loadAccount() {
  try {
    const u = state.user || (await api.get("/api/auth/me"));
    state.user = u;
    $("a-name").value = u.display_name || "";
    $("a-email").value = u.email || "";
    await loadPrefs();
    const p = state.prefs || {};
    $("p-voice").checked = p.voice_enabled !== false;
    $("p-autorecalc").checked = p.auto_recalculate !== false;
    $("p-hours").value = p.available_drive_hours ?? "";
    $("p-break").value = p.break_interval_hours ?? "";
    $("p-style").value = p.map_style || "dark";
    const sub = await api.get("/api/subscription/status");
    $("sub-status").innerHTML = `<strong>${escapeHtml(sub.plan.toUpperCase())}</strong> — ${escapeHtml(sub.status)}${sub.is_premium ? ' <span class="badge">PREMIUM</span>' : ""}`;
    $("sub-price").textContent = sub.price_monthly_usd != null
      ? `Premium is $${sub.price_monthly_usd}/mo or $${sub.price_annual_usd}/yr (configurable).`
      : "";
  } catch (e) { toast(e.message); }
}
$("btn-save-profile").onclick = async () => {
  try {
    state.user = await api.put("/api/auth/me", {
      display_name: $("a-name").value.trim(), email: $("a-email").value.trim(),
    });
    toast("Profile saved");
  } catch (e) { toast(e.message); }
};
$("btn-save-prefs").onclick = async () => {
  try {
    state.prefs = await api.put("/api/preferences", {
      voice_enabled: $("p-voice").checked,
      auto_recalculate: $("p-autorecalc").checked,
      available_drive_hours: $("p-hours").value ? parseFloat($("p-hours").value) : null,
      break_interval_hours: $("p-break").value ? parseFloat($("p-break").value) : null,
      map_style: $("p-style").value,
    });
    setMapStyle(state.prefs.map_style || "dark");
    toast("Preferences saved");
  } catch (e) { toast(e.message); }
};
$("btn-export").onclick = async () => {
  try {
    const data = await api.get("/api/auth/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "truckroute-data.json";
    a.click();
    toast("Data downloaded");
  } catch (e) { toast(e.message); }
};
$("btn-change-pw").onclick = async () => {
  const cur = prompt("Current password:");
  if (!cur) return;
  const nw = prompt("New password (8+ characters):");
  if (!nw) return;
  try { await api.post("/api/auth/change-password", { current_password: cur, new_password: nw }); toast("Password changed"); }
  catch (e) { toast(e.message); }
};
$("btn-logout").onclick = () => { api.clear(); showScreen("auth"); };
$("btn-delete-account").onclick = async () => {
  if (!confirm("Delete your account and ALL data? This cannot be undone.")) return;
  if (!confirm("Last chance — really delete everything?")) return;
  try { await api.del("/api/auth/me"); api.clear(); showScreen("auth"); toast("Account deleted"); }
  catch (e) { toast(e.message); }
};

/* ================= admin ================= */
async function loadAdmin() {
  try {
    const h = await api.get("/api/admin/health");
    $("admin-providers").innerHTML =
      `Routing: <strong>${escapeHtml(h.routing_provider)}</strong><br>Geocoding: <strong>${escapeHtml(h.geocode_provider)}</strong><br>HERE key: ${h.here_configured ? "configured" : "missing — using dev providers"}`;
    const u = await api.get("/api/admin/usage?days=30");
    let html = `<p><strong>${u.total_calls}</strong> API calls · <strong>$${u.total_cost_usd.toFixed(2)}</strong> est. cost · <strong>${u.active_users}</strong> active users · <strong>${u.avg_calls_per_user}</strong> avg calls/user</p>`;
    html += "<p><strong>By provider:</strong><br>" + (u.by_provider.map((p) => `${escapeHtml(p.provider)}: ${p.calls} calls ($${p.cost_usd})`).join("<br>") || "—") + "</p>";
    html += "<p><strong>By operation:</strong><br>" + (u.by_operation.map((p) => `${escapeHtml(p.operation)}: ${p.calls} calls ($${p.cost_usd})`).join("<br>") || "—") + "</p>";
    $("admin-usage").innerHTML = html;
    const errs = await api.get("/api/admin/errors?limit=10");
    $("admin-errors").innerHTML = errs.length
      ? errs.map((e) => `<div class="s"><strong>${escapeHtml(e.where)}</strong> — ${escapeHtml(e.message.slice(0, 120))}<br><span style="color:var(--muted)">${escapeHtml(e.created_at || "")}</span></div>`).join("")
      : "No errors recorded.";
  } catch (e) {
    $("admin-providers").textContent = "Admin access required.";
  }
}

/* ================= helpers ================= */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ================= boot ================= */
(async function boot() {
  buildReportGrid();
  if (api.access) {
    try {
      state.user = await api.get("/api/auth/me");
      $("nav-admin").hidden = !state.user.is_admin;
      await loadPrefs();
      showScreen("map");
      initMapOnce();
      startGps();
      startIncidentFeed();
      return;
    } catch (e) { api.clear(); }
  }
  showScreen("auth");
})();
