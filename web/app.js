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
  dest: null, route: null, routeId: null, selOption: 0,
  navigating: false, sessionId: null,
  pos: null, // {lat, lng, speed, bearing}
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
  map.on("load", () => {
    map.addSource("route", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-line", type: "line", source: "route",
      paint: { "line-color": "#3b82f6", "line-width": 6, "line-opacity": 0.95 } });
    map.addSource("route-done", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-done-line", type: "line", source: "route-done",
      paint: { "line-color": "#93a3bd", "line-width": 6, "line-opacity": 0.9 } });
  });
}
function setMapStyle(name) {
  mapStyle = name;
  const keep = state.route ? state.route.options[state.selOption] : null;
  map.setStyle(STYLES[name] || STYLES.dark);
  map.once("style.load", () => {
    map.addSource("route", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-line", type: "line", source: "route",
      paint: { "line-color": "#3b82f6", "line-width": 6, "line-opacity": 0.95 } });
    map.addSource("route-done", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "route-done-line", type: "line", source: "route-done",
      paint: { "line-color": "#93a3bd", "line-width": 6, "line-opacity": 0.9 } });
    if (keep) drawRoute(keep.shape);
    if (gpsMarker) gpsMarker.addTo(map);
    if (destMarker) destMarker.addTo(map);
  });
}
$("btn-style").onclick = () => {
  const order = ["dark", "light", "satellite"];
  setMapStyle(order[(order.indexOf(mapStyle) + 1) % order.length]);
  toast("Map: " + mapStyle);
};

let gpsMarker = null, destMarker = null;
function updateGpsMarker() {
  if (!map || !state.pos) return;
  const el = document.createElement("div");
  el.style.cssText = "width:20px;height:20px;border-radius:50%;background:#3b82f6;border:3px solid #fff;box-shadow:0 0 10px rgba(59,130,246,.8)";
  if (!gpsMarker) {
    gpsMarker = new maplibregl.Marker({ element: el }).setLngLat([state.pos.lng, state.pos.lat]).addTo(map);
    map.flyTo({ center: [state.pos.lng, state.pos.lat], zoom: 13 });
  } else {
    gpsMarker.setLngLat([state.pos.lng, state.pos.lat]);
  }
}
$("btn-recenter").onclick = () => {
  if (state.pos && map) map.flyTo({ center: [state.pos.lng, state.pos.lat], zoom: 15 });
  else toast("No GPS fix yet");
};

/* ================= GPS + voice ================= */
let watchId = null, pollTimer = null;
function startGps() {
  if (NATIVE) {
    try { window.TruckRoute.startGps(); } catch (e) {}
    pollTimer = setInterval(pollNativeGps, 2000);
    pollNativeGps();
  } else if ("geolocation" in navigator) {
    watchId = navigator.geolocation.watchPosition(
      (p) => {
        state.pos = { lat: p.coords.latitude, lng: p.coords.longitude,
          speed: p.coords.speed || 0, bearing: p.coords.heading || 0 };
        $("gps-banner").hidden = true;
        updateGpsMarker();
        if (state.navigating) navUpdate();
      },
      () => { $("gps-banner").hidden = false; $("gps-banner").textContent = "GPS unavailable — check location permission"; },
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 15000 }
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
    state.pos = { lat: p.lat, lng: p.lng, speed: p.speed || 0, bearing: p.bearing || 0 };
    $("gps-banner").hidden = true;
    updateGpsMarker();
    if (state.navigating) navUpdate();
  } catch (e) {}
}
function speak(text) {
  if (state.prefs && state.prefs.voice_enabled === false) return;
  if (NATIVE) { try { window.TruckRoute.speak(text); return; } catch (e) {} }
  try {
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  } catch (e) {}
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
  $("search-dest").value = it.label;
  state.dest = { lat: it.lat, lng: it.lng, label: it.label };
  if (destMarker) destMarker.remove();
  destMarker = new maplibregl.Marker({ color: "#f5a623" })
    .setLngLat([it.lng, it.lat]).addTo(map);
  calculateRoute();
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
function clearRoute() {
  state.route = null; state.dest = null;
  if (map && map.getSource("route"))
    map.getSource("route").setData({ type: "FeatureCollection", features: [] });
  if (map && map.getSource("route-done"))
    map.getSource("route-done").setData({ type: "FeatureCollection", features: [] });
  if (destMarker) { destMarker.remove(); destMarker = null; }
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
const nav = { maneuvers: [], coords: [], idx: 0, announced: {}, offCount: 0, lastFix: 0, startTime: 0 };

$("btn-start-nav").onclick = async () => {
  const o = state.route.options[state.selOption];
  $("modal-summary").hidden = true;
  nav.maneuvers = o.maneuvers; nav.coords = o.shape.coordinates;
  nav.idx = 0; nav.announced = {}; nav.offCount = 0;
  state.navigating = true;
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
  if (now - nav.lastFix < 2500) return; // don't thrash
  nav.lastFix = now;
  const { lat, lng } = state.pos;
  const near = nearestOnRoute(lat, lng);

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
    speak(m.instruction);
  }

  // HUD
  $("nav-instruction").textContent = m.instruction;
  $("nav-dist").textContent = dTo < 1609 ? `${Math.round(dTo * 3.281)} ft` : `${(dTo / 1609.344).toFixed(1)} mi`;
  const remMi = remainingMiles(near.idx);
  $("nav-remain").textContent = remMi.toFixed(0);
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
  // follow mode
  if (map && !map._userMoved) map.setCenter([lng, lat]);
}
if (typeof maplibregl !== "undefined") {
  // track manual pans so follow mode doesn't fight the driver
  document.addEventListener("pointerdown", () => { if (map) map._userMoved = true; }, true);
}

/* ================= trucks ================= */
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
  if (api.access) {
    try {
      state.user = await api.get("/api/auth/me");
      $("nav-admin").hidden = !state.user.is_admin;
      await loadPrefs();
      showScreen("map");
      initMapOnce();
      startGps();
      return;
    } catch (e) { api.clear(); }
  }
  showScreen("auth");
})();
