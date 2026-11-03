const { test, expect } = require("@playwright/test");

// Konfiguration in der Form, die /api/config liefert (Schema 5).
const baseConfig = {
  app: { name: "StorchCam", language: "de", timezone: "Europe/Berlin", cache_dir: "~/.cache/StorchCam", config_schema_version: 5 },
  server: { host: "127.0.0.1", port: 8000, max_request_bytes: 262144, admin_session_minutes: 30, public_url: "", lan_access: false },
  kiosk: { enabled: true, browser: "auto", profile_dir: "", disable_screensaver: true, use_gpu: true, browser_restart_seconds: 3, browser_restart_max_seconds: 60, browser_stable_seconds: 30, browser_max_failures: 8, log_file: "~/.cache/StorchCam/chromium.log", log_max_bytes: 2097152, log_backups: 3, extra_flags: [] },
  screen: { hardware_profile: "generic", output: "auto", rotation: "none", touch_device: "", touch_matrix: "" },
  location: { label: "Musterstadt", latitude: 52.5200, longitude: 13.4050 },
  stream: { url: "http://127.0.0.1:4173/mock-stream", muted: true, autoplay: true, quality: "auto", max_fps: 30, audio: "off", favorites: [], auto_fallback: true, notes: {} },
  admin: { pin_hash: "", require_pin: true },
  youtube: { api_key: "", default_query: "Storch" },
  music: { source: "off", spotify: { client_id: "" }, radio: { favorites: [] } },
  ui: {
    theme: "dark", sky: "auto", layout_profile: "auto", distance: 2, compact_after: 120, setup_done: true,
    night: { enabled: false, start: "23:00", end: "06:00", screen_off: false },
    clock: { enabled: true }, weather: { enabled: true }, transit: { enabled: true }, system: { enabled: true },
  },
  weather: { provider: "openmeteo", refresh_seconds: 300, forecast_hours: 8, rain_mm_threshold: 0.1, rain_probability_threshold: 45, warnings: "moderate" },
  transit: {
    provider: "vrr", refresh_seconds: 60, default_max_rows: 2, target_len: 16, leave_mode: true,
    stops: [{ title: "", station_name: "Musterstadt, Hauptstraße", station_id: "20000001", line_filter: [], nightbus_only: false, hide_if_empty: true, max_rows: 3, walk_minutes: 5 }],
  },
  logging: { level: "INFO", max_bytes: 2097152, backups: 3 },
};

// Gleiche Richtlinie wie server.py, damit CSP-Verstöße auch unter dem statischen Testserver auffallen.
const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; "
  + "img-src 'self' data: https://i.ytimg.com https://i.scdn.co https://*.googleusercontent.com https://yt3.ggpht.com; "
  + "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'";

const RAIN = [10, 20, 60, 75, 40, 15, 5, 5, 10, 20, 30, 20, 10, 5, 5, 10, 15, 20, 25, 20, 15, 10, 5, 5, 5];

function weather(stale = false) {
  const start = new Date();
  start.setMinutes(0, 0, 0);
  const hourly = RAIN.map((probability, index) => {
    const at = new Date(start.getTime() + index * 3600e3);
    return { time: at.toISOString(), hour: at.getHours(), temp: 18 + Math.round(3 * Math.cos(index / 4)), probability, rain_mm: probability >= 45 ? 0.6 : 0, wind: 12 + (index % 4), code: probability >= 45 ? 61 : 2, is_day: at.getHours() >= 7 && at.getHours() < 19 };
  });
  const day = new Date();
  const sun = [0, 1].map((offset) => ({
    rise: new Date(day.getFullYear(), day.getMonth(), day.getDate() + offset, 6, 40).toISOString(),
    set: new Date(day.getFullYear(), day.getMonth(), day.getDate() + offset, 18, 20).toISOString(),
  }));
  return { ok: true, label: "Musterstadt", text: "", condition: "teils bewölkt", code: 2, is_day: true, temp: 21, feels: 19, wind: 14, hum: 78, rain: 0, forecast: [], hourly, sun, rain_threshold: 45, updated_at: new Date().toISOString(), stale };
}

function departure(line, type, target, minutes, delay = 0) {
  const now = Math.floor(Date.now() / 1000);
  return { line, type, target, mins: `${minutes} min`, minutes, delay, realtime: true, planned: now + (minutes - delay) * 60, at: now + minutes * 60, platform: "" };
}

function boards() {
  return [{
    title: "Hauptstraße", station_id: "20000001", station_name: "Musterstadt, Hauptstraße", ok: true, walk_minutes: 5, hide_if_empty: true,
    checked_at: new Date().toISOString(),
    rows: [departure("31", "bus", "Nordpark", 6, 2), departure("4", "tram", "Südring", 14), departure("N2", "night", "Westpark", 75)],
    lines: [{ line: "4", type: "tram" }, { line: "31", type: "bus" }, { line: "N2", type: "night" }],
  }];
}

function stateFor(config, overrides = {}) {
  return {
    generated_at: new Date().toISOString(),
    config: {
      ui: config.ui, screen: config.screen, stream: config.stream, location: config.location, timezone: config.app.timezone,
      transit_provider: "vrr", transit_stop_count: config.transit.stops.length, transit_leave_mode: config.transit.leave_mode,
      music_source: config.music.source, warnings: config.weather.warnings,
    },
    weather: weather(),
    boards: boards(),
    system: { cpu: 12, ram: 42, temp: 51, uptime: "2h 3m" },
    music: { enabled: false },
    warnings: { ok: true, enabled: true, covered: true, alerts: [] },
    health: {},
    ...overrides,
  };
}

const LIVE_RESULTS = [
  { video_id: "rurvbn9yH-k", title: "Live aus dem Storchennest in Leutershausen", channel: "Heimatverein Leutershausen", thumbnail: "", embed_url: "https://www.youtube.com/embed/rurvbn9yH-k" },
  { video_id: "W2CicgFq5oo", title: "Live Storchennest Kirchzarten", channel: "regiowebcam", thumbnail: "", embed_url: "https://www.youtube.com/embed/W2CicgFq5oo" },
];
const STATIONS = [
  { uuid: "11111111-aaaa", name: "WDR 2", url: "http://radio.example.org/wdr2.mp3", country: "Germany", state: "", codec: "MP3", bitrate: 128, tags: ["pop", "news"], votes: 900 },
  { uuid: "22222222-bbbb", name: "Deutschlandfunk", url: "http://radio.example.org/dlf.mp3", country: "Germany", state: "", codec: "MP3", bitrate: 128, tags: ["news"], votes: 800 },
];

async function mockBackend(page, { config = structuredClone(baseConfig), overrides = {}, open = true } = {}) {
  let savedConfig = config;
  let state = overrides;
  const cspErrors = [];
  const pageErrors = [];
  const calls = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/i.test(message.text())) cspErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.route("http://127.0.0.1:4173/**", async (route) => {
    const request = route.request();
    if (request.resourceType() !== "document" || request.frame() !== page.mainFrame()) return route.fallback();
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy": CSP, "x-content-type-options": "nosniff" } });
  });
  const json = (pattern, body) => page.route(pattern, (route) => {
    calls.push([route.request().url(), route.request().postDataJSON?.() ?? null]);
    return route.fulfill({ json: typeof body === "function" ? body(route.request()) : body });
  });
  await json("**/api/state", () => stateFor(savedConfig, state));
  await json("**/api/admin/status", () => (open
    ? { ok: true, pin_required: false, pin_configured: true, authenticated: true, remote: false, pin_login: true }
    : { ok: true, pin_required: true, pin_configured: false, authenticated: false, remote: false, pin_login: true }));
  await json("**/api/stream/source*", { kind: "embed", reason: "Test" });
  await json("**/api/live/search?*", { query: "Storch", results: LIVE_RESULTS });
  await json("**/api/location/search?*", { query: "Tokio", results: [{ name: "Tokio", region: "Japan", latitude: 35.6895, longitude: 139.6917, timezone: "Asia/Tokyo" }] });
  await json("**/api/station/search?*", { query: "Marktplatz", results: [{ station_id: "20000002", station_name: "Musterstadt Marktplatz" }] });
  await json("**/api/admin/setup", { ok: true, token: "test-token", expires_in: 1800 });
  await json("**/api/admin/login", { ok: true, token: "test-token", expires_in: 1800 });
  await json("**/api/config", () => ({ path: "/tmp/config.json", config: savedConfig, youtube_key_set: false, spotify_connected: false, ytmusic_connected: false }));
  await json("**/api/config/save", (request) => {
    savedConfig = request.postDataJSON().config;
    return { ok: true, path: "/tmp/config.json", config: savedConfig };
  });
  await json("**/api/radio/search", { ok: true, results: STATIONS });
  await json("**/api/radio/play", (request) => ({ ok: true, station: request.postDataJSON().station }));
  await json("**/api/music/recent", { ok: true, results: [] });
  await json("**/api/system/info", { ok: true, version: "test", uptime_seconds: 60, config_saved: "", backlight: false, public_url: "", remote: false });
  await json("**/api/pair/**", { ok: true, active: true, code: "123456", expires_in: 600, url: "", claimed: false });
  await json("**/api/bluetooth", { ok: true, available: false, error: "Bluetooth nicht verfügbar", devices: [] });
  await json("**/api/music/**", { ok: true, status: "idle", connected: false, paired: false });
  await json("**/api/display/power", { ok: true, switched: false });
  await page.route("**/mock-stream?*", (route) => route.fulfill({
    contentType: "text/html",
    body: "<title>Mock stream</title><body style=\"margin:0;height:100vh;background:linear-gradient(180deg,#24343b,#121c20 55%,#1d2a24)\"></body>",
  }));
  return { getSaved: () => savedConfig, setState: (value) => { state = value; }, cspErrors, pageErrors, calls };
}

function inside(box, viewport) {
  return box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1;
}

function overlaps(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

async function openMenu(page) {
  await page.getByRole("button", { name: "Menü öffnen" }).click();
  await expect(page.locator("#sheet")).toBeVisible();
  await expect(page.locator('.page[data-panel="home"]')).toBeVisible();
}

// Startseite des Menüs: Kachel antippen, die Seite schiebt sich darüber.
async function openTile(page, name) {
  await page.locator(`#homeTiles [data-open="${name}"]`).click();
}

const visiblePage = (page, name) => page.locator(`.page[data-panel="${name}"]`);

for (const viewport of [{ width: 800, height: 480 }, { width: 1024, height: 600 }, { width: 1280, height: 720 }, { width: 1920, height: 1080 }, { width: 720, height: 1280 }]) {
  test(`info bar at ${viewport.width}x${viewport.height} shows clock, weather strip and leave times`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const { cspErrors, pageErrors } = await mockBackend(page);
    await page.goto("/");
    await expect(page.locator("#clock")).toHaveText(/^\d{2}:\d{2}$/);
    await expect(page.locator("#weatherTemp")).toHaveText("21°");
    await expect.poll(() => page.locator("#strip rect").count()).toBeGreaterThan(10);
    await expect(page.locator("#boards .departure").first()).toBeVisible();
    // Bus in 6 Minuten, 5 Minuten Weg: jetzt losgehen.
    await expect(page.locator("#boards .departure").first()).toContainText("Jetzt los");
    await expect(page.locator("#boards .delay").first()).toHaveText("+2");
    const bar = await page.locator("#infobar").boundingBox();
    expect(inside(bar, viewport)).toBe(true);
    for (const selector of ["#clockBlock", "#weatherBlock", "#transitBlock"]) {
      const box = await page.locator(selector).boundingBox();
      expect(inside(box, viewport), selector).toBe(true);
    }
    const menu = await page.locator("#menuBtn").boundingBox();
    expect(overlaps(menu, bar)).toBe(false);
    expect(cspErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
  });
}

test("plain departure times without leave mode and honest error states", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const config = structuredClone(baseConfig);
  config.transit.leave_mode = false;
  const failing = [{ title: "Rathaus", station_id: "9", station_name: "Musterstadt, Rathaus", ok: false, error: "Zeitüberschreitung", rows: [], lines: [], walk_minutes: 0, checked_at: new Date().toISOString() }];
  await mockBackend(page, { config, overrides: { boards: [...boards(), ...failing], weather: weather(true) } });
  await page.goto("/");
  await expect(page.locator("#trTitle")).toHaveText("Abfahrten");
  const first = page.locator("#boards .departure:not(.is-error)").first();
  await expect(first).not.toContainText("Jetzt los");
  await expect(first.locator(".dep-num")).toHaveText(/^[56]$/);
  await expect(page.locator("#boards .departure.is-error")).toContainText("Rathaus");
  await expect(page.locator("#weatherBlock")).toHaveClass(/is-stale/);
});

test("official DWD warning appears above the bar and opens its details", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const alert = { id: "a", headline: "Amtliche Warnung vor Sturmböen", event: "STURMBÖEN", severity: "moderate", level: 2, onset: new Date().toISOString(), expires: new Date(Date.now() + 3 * 3600e3).toISOString(), description: "Sturmböen um 70 km/h.", instruction: "" };
  await mockBackend(page, { overrides: { warnings: { ok: true, enabled: true, covered: true, alerts: [alert] } } });
  await page.goto("/");
  await expect(page.locator("#warning")).toBeVisible();
  await expect(page.locator("#warningText")).toContainText("Sturmböen");
  await page.locator("#warning").click();
  await expect(page.locator("#detailWarning")).toBeVisible();
  await expect(page.locator("#dwarnBody")).toContainText("70 km/h");
});

test("details for weather and departures open by tapping the bar", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockBackend(page);
  await page.goto("/");
  await page.locator("#weatherBlock").click();
  await expect(page.locator("#detailWeather")).toBeVisible();
  await expect.poll(() => page.locator("#wchart polyline").count()).toBeGreaterThan(0);
  await page.locator("#detailWeather [data-close]").click();
  await expect(page.locator("#detailWeather")).toBeHidden();
  await page.locator("#transitBlock").click();
  await expect(page.locator("#detailDepart")).toBeVisible();
  await expect(page.locator("#boardTable tbody tr")).toHaveCount(3);
});

test("menu opens without PIN; a theme applies at once and can be undone", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const { getSaved, cspErrors } = await mockBackend(page);
  await page.goto("/");
  await openMenu(page);
  await expect(page.locator("#pinDialog")).toBeHidden();
  await openTile(page, "display");
  await page.locator('.theme-card[data-theme="flap"]').click();
  await expect(page.locator("body")).toHaveAttribute("data-theme", "flap");
  expect(getSaved().ui.theme).toBe("flap");
  await expect(page.locator("#toastUndo")).toBeVisible();
  await page.locator("#toastUndo").click();
  await expect(page.locator("body")).toHaveAttribute("data-theme", "dark");
  expect(getSaved().ui.theme).toBe("dark");
  await page.locator('#distSeg [data-dist="3"]').click();
  await expect.poll(() => getSaved().ui.distance).toBe(3);
  expect(cspErrors).toEqual([]);
});

test("first use asks for a new PIN before the menu opens", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockBackend(page, { open: false });
  await page.goto("/");
  await page.getByRole("button", { name: "Menü öffnen" }).click();
  await expect(page.locator("#pinDialog")).toBeVisible();
  await expect(page.locator("#pinTitle")).toHaveText("Admin-PIN erstmals festlegen");
  await page.locator("#adminPin").fill("2468");
  await page.locator("#adminPinConfirm").fill("2468");
  await page.locator("#submitPin").click();
  await expect(page.locator("#pinDialog")).toBeHidden();
  await expect(page.locator("#sheet")).toBeVisible();
});

test("live search plays by tapping and the star keeps a favorite", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const { getSaved } = await mockBackend(page);
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "live");
  await expect(page.locator("#liveResults .result")).toHaveCount(2);
  await page.locator("#liveResults .result").nth(0).locator(".star").click();
  await expect.poll(() => getSaved().stream.favorites.length).toBe(1);
  expect(getSaved().stream.favorites[0].title).toBe("Live aus dem Storchennest in Leutershausen");
  await page.locator("#liveResults .result").nth(1).click();
  await expect.poll(() => getSaved().stream.url).toBe("https://www.youtube.com/embed/W2CicgFq5oo");
  await expect(page.locator("#favList")).toContainText("Leutershausen");
});

test("place search moves clock and weather to the chosen place", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const { getSaved } = await mockBackend(page);
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "place");
  await page.locator("#placeQuery").click();
  await expect(page.locator("#osk")).toBeVisible();
  for (const key of ["t", "o", "k", "i", "o"]) await page.locator(`#osk [data-key="${key}"]`).click();
  await expect(page.locator("#placeQuery")).toHaveValue("Tokio");
  await page.locator('#osk [data-key="enter"]').click();
  await page.locator("#placeResults .pick").first().click();
  await expect.poll(() => getSaved().location.label).toBe("Tokio");
  const saved = getSaved();
  expect([saved.location.latitude, saved.location.longitude, saved.app.timezone]).toEqual([35.6895, 139.6917, "Asia/Tokyo"]);
});

test("stops are added from the search and the walk time is saved", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const { getSaved } = await mockBackend(page);
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "transit");
  await page.locator("#stopQuery").fill("Marktplatz");
  await page.locator("#stopSearchBtn").click();
  await page.locator("#stopResults .pick").first().click();
  await expect.poll(() => getSaved().transit.stops.length).toBe(2);
  expect(getSaved().transit.stops[1].station_id).toBe("20000002");
  await page.locator("#stopCards .card").first().getByRole("button", { name: "Eine Minute mehr" }).click();
  await expect.poll(() => getSaved().transit.stops[0].walk_minutes).toBe(6);
});

test("radio search plays a station and the star keeps it as favourite", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const { getSaved, calls } = await mockBackend(page);
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "player");
  await expect(visiblePage(page, "music")).toBeVisible();
  await expect(page.locator("#mResults .mrow")).toHaveCount(2);
  await page.locator("#mResults .mrow").first().click();
  await expect.poll(() => calls.filter(([url]) => url.endsWith("/api/radio/play")).length).toBe(1);
  expect(calls.find(([url]) => url.endsWith("/api/radio/play"))[1].station.uuid).toBe("11111111-aaaa");
  await page.locator("#mResults .mrow").nth(1).locator(".fav").click();
  await expect.poll(() => getSaved().music.radio.favorites.length).toBe(1);
  expect(getSaved().music.radio.favorites[0].name).toBe("Deutschlandfunk");
});

test("radio now playing card shows the station and the current song", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const music = { enabled: true, ok: true, playing: true, controls: "pi", source: "radio", device: "pi", title: "WDR 2", artist: "Coldplay – Viva la Vida", cover: "", station: STATIONS[0], live: true, progress_ms: 0, duration_ms: 0, paused: false, volume: 60, fetched_at: Date.now(), sleep_end: 0 };
  await mockBackend(page, { overrides: { music } });
  await page.goto("/");
  await expect(page.locator("#nowPlaying")).toBeVisible();
  await expect(page.locator("#npTitle")).toHaveText("WDR 2");
  await expect(page.locator("#npSub")).toContainText("Coldplay");
  await expect(page.locator("#nowPlaying")).toHaveClass(/is-radio/);
});

test("volume is set right on the song card, also when it is closed", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const music = { enabled: true, ok: true, playing: true, controls: "pi", source: "radio", device: "pi", title: "WDR 2", artist: "Coldplay – Viva la Vida", cover: "", station: STATIONS[0], live: true, progress_ms: 0, duration_ms: 0, paused: false, volume: 60, fetched_at: Date.now(), sleep_end: 0 };
  const { calls } = await mockBackend(page, { overrides: { music } });
  await page.route("**/api/player/control", (route) => { calls.push([route.request().url(), route.request().postDataJSON()]); return route.fulfill({ json: { ok: true } }); });
  await page.goto("/");
  await expect(page.locator("#npVol")).toBeHidden();
  await page.locator("#npVolBtn").click();
  await expect(page.locator("#npVol")).toBeVisible();
  await page.getByRole("button", { name: "Lauter" }).click();
  await expect(page.locator("#npVolOut")).toHaveText("65");
  await expect.poll(() => calls.filter(([url]) => url.endsWith("/api/player/control")).map(([, body]) => [body.action, body.value])).toEqual([["volume", 65]]);
  // Ein Tipp daneben klappt den Regler wieder zu.
  await page.locator("#clockBlock").click();
  await expect(page.locator("#npVol")).toBeHidden();
  // Aufgeklappt gehört der Regler dazu.
  await page.locator("#npOpen").click();
  await expect(page.locator("#npVol")).toBeVisible();
});

test("the info bar shrinks to the compact bar and comes back", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockBackend(page);
  await page.goto("/");
  await expect(page.locator("#infobar")).toBeVisible();
  await page.locator("#barHandle").click();
  await expect(page.locator("#minibar")).toBeVisible();
  await expect(page.locator("#infobar")).toBeHidden();
  await expect(page.locator("#mbClock")).toHaveText(/\d{2}:\d{2}/);
  await page.locator("#minibar").click();
  await expect(page.locator("#infobar")).toBeVisible();
});

test("first start without a stream opens the setup wizard", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const config = structuredClone(baseConfig);
  config.stream.url = "";
  config.ui.setup_done = false;
  const { getSaved } = await mockBackend(page, { config });
  await page.goto("/");
  await expect(page.locator("#wizard")).toBeVisible();
  await expect(page.locator("#wzTitle")).toContainText("Kamera");
  await page.locator("#wzSkip").click();
  await expect(page.locator("#wizard")).toBeHidden();
  await expect.poll(() => getSaved().ui.setup_done).toBe(true);
});

test("night mode dims the screen and a tap wakes it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const config = structuredClone(baseConfig);
  config.ui.night = { enabled: true, start: "00:00", end: "23:59", screen_off: false };
  await mockBackend(page, { config });
  await page.goto("/");
  await expect(page.locator("#night")).toBeVisible();
  await expect(page.locator("#nightClock")).toHaveText(/\d{2}:\d{2}/);
  await page.locator("#night").click();
  await expect(page.locator("#night")).toBeHidden();
});

test("YouTube and HLS streams play in the own video player instead of an iframe", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const { cspErrors } = await mockBackend(page);
  let playlistRequests = 0;
  await page.route("**/api/stream/source*", (route) => route.fulfill({ json: { kind: "hls", url: "/mock-hls/index.m3u8", title: "Hafen live", channel: "Hafencam", live: true, expires_in: 3600 } }));
  await page.route("**/mock-hls/index.m3u8", (route) => {
    playlistRequests += 1;
    return route.fulfill({ contentType: "application/vnd.apple.mpegurl", body: "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nseg0.ts\n" });
  });
  await page.route("**/mock-hls/seg0.ts", (route) => route.fulfill({ contentType: "video/mp2t", body: Buffer.alloc(188 * 8) }));
  await page.goto("/");
  await expect(page.locator("#player")).toHaveClass(/is-video/);
  await expect(page.locator("#live")).toBeHidden();
  expect(await page.locator("#live").getAttribute("src")).toBeNull();
  await expect.poll(() => playlistRequests).toBeGreaterThan(0);
  // Das Video nimmt keine Berührung an, ein Tippen aufs Bild kann es also nicht anhalten.
  expect(await page.locator("#liveVideo").evaluate((video) => getComputedStyle(video).pointerEvents)).toBe("none");
  await expect(page.locator("#chipTitle")).toHaveText("Hafen live");
  expect(cspErrors).toEqual([]);
});

const SONG = (id, title, seconds = 200) => ({ id, title, artist: "Coldplay", cover: "", duration: seconds, liked: null });
const ALBUM_PAGE = { kind: "album", id: "MPREpara", title: "Parachutes", subtitle: "Coldplay · 2000", cover: "", tracks: [SONG("s1", "Don't Panic"), SONG("s2", "Shiver"), SONG("s3", "Yellow")], albums: [] };
const ARTIST_PAGE = { kind: "artist", id: "UCcold", title: "Coldplay", subtitle: "Künstler", cover: "", tracks: [SONG("s3", "Yellow"), SONG("s4", "Clocks")], albums: [{ kind: "album", id: "MPREpara", title: "Parachutes", subtitle: "2000", cover: "" }], play_id: "VLOLAKsongs" };
const SEARCH = [
  { kind: "artist", id: "UCcold", title: "Coldplay", subtitle: "", cover: "" },
  { kind: "song", id: "s4", title: "Clocks", subtitle: "Coldplay", cover: "", duration: 307 },
  { kind: "album", id: "MPREpara", title: "Parachutes", subtitle: "Coldplay", cover: "" },
];

async function mockLibrary(page) {
  const calls = [];
  const json = (pattern, body) => page.route(pattern, (route) => {
    calls.push([new URL(route.request().url()).pathname, route.request().postDataJSON?.() ?? null]);
    return route.fulfill({ json: typeof body === "function" ? body(route.request()) : body });
  });
  await json("**/api/config", (request) => ({ path: "/tmp/config.json", config: baseConfig, youtube_key_set: false, spotify_connected: false, ytmusic_connected: true }));
  await json("**/api/player/search", { ok: true, results: SEARCH });
  await json("**/api/player/playlists", { ok: true, results: [{ kind: "playlist", id: "LM", title: "Liked Music", subtitle: "", cover: "" }, { kind: "playlist", id: "PLauto", title: "Autofahrt", subtitle: "48 Songs", cover: "" }] });
  await json("**/api/player/open", (request) => ({ ok: true, results: request.postDataJSON().kind === "artist" ? ARTIST_PAGE : ALBUM_PAGE }));
  for (const name of ["play", "control", "like", "add"]) await json(`**/api/player/${name}`, { ok: true });
  await json("**/api/player/level", { ok: true, level: 0.6 });
  return calls;
}

const played = (calls) => calls.filter(([path]) => path === "/api/player/play").map(([, body]) => body);

test("music search opens albums and artists as pages; a song plays alone, a page plays from the tapped song", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const { cspErrors, pageErrors } = await mockBackend(page);
  const calls = await mockLibrary(page);
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "player");
  await page.locator('#srcSeg [data-src="ytmusic"]').click();
  await page.locator("#mQuery").fill("coldplay");
  await page.locator("#mSearch").click();
  await expect(page.locator("#mResults .group-head").first()).toHaveText("Künstler");
  // Ein Song spielt nur sich selbst.
  await page.locator("#mResults .mrow", { hasText: "Clocks" }).click();
  await expect.poll(() => played(calls).length).toBe(1);
  expect(played(calls)[0]).toMatchObject({ service: "ytmusic", kind: "song", id: "s4" });
  // Ein Album öffnet eine Seite statt sofort zu spielen.
  await page.locator("#mResults .mrow", { hasText: "Parachutes" }).click();
  const albumPage = page.locator(".page.is-dynamic");
  await expect(albumPage.locator(".page-text strong")).toHaveText("Parachutes");
  await expect(visiblePage(page, "music")).toBeHidden();
  expect(played(calls).length).toBe(1);
  await albumPage.locator(".track-row").nth(1).click();
  await expect.poll(() => played(calls).length).toBe(2);
  expect(played(calls)[1]).toMatchObject({ kind: "album", id: "MPREpara", start: 1, shuffle: false });
  await albumPage.getByRole("button", { name: "Zufall" }).click();
  await expect.poll(() => played(calls).length).toBe(3);
  expect(played(calls)[2]).toMatchObject({ kind: "album", start: 0, shuffle: true });
  await albumPage.locator("[data-back]").click();
  await expect(visiblePage(page, "music")).toBeVisible();
  await expect(page.locator(".page.is-dynamic")).toHaveCount(0);
  // Künstler: beliebte Titel, Alben als Kacheln; „Abspielen“ spielt die Songliste des Künstlers.
  await page.locator("#mResults .mrow", { hasText: "Coldplay" }).first().click();
  const artistPage = page.locator(".page.is-dynamic");
  await expect(artistPage.locator(".tiles .tile")).toHaveCount(1);
  await artistPage.getByRole("button", { name: "Abspielen" }).click();
  await expect.poll(() => played(calls).length).toBe(4);
  expect(played(calls)[3]).toMatchObject({ kind: "playlist", id: "VLOLAKsongs", start: 0 });
  // Album aus der Künstlerseite öffnet eine weitere Seite; der Musik-Reiter führt zurück zum Anfang.
  await artistPage.locator(".tiles .tile").click();
  await expect(page.locator(".page.is-dynamic")).toHaveCount(2);
  // Zurück per Wisch vom linken Rand
  const box = await page.locator("#sheet").boundingBox();
  await page.mouse.move(box.x + 8, box.y + 300);
  await page.mouse.down();
  for (let step = 1; step <= 10; step += 1) await page.mouse.move(box.x + 8 + step * 45, box.y + 304, { steps: 2 });
  await page.mouse.up();
  await expect(page.locator(".page.is-dynamic")).toHaveCount(1);
  expect(cspErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test("player tab: queue, shuffle, repeat, seek, like, playlist and equalizer", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const queue = [SONG("s1", "Don't Panic"), SONG("s2", "Shiver"), SONG("s3", "Yellow"), SONG("s4", "Clocks")].map((track, position) => ({ position, ...track }));
  const music = { enabled: true, ok: true, playing: true, controls: "pi", source: "ytmusic", device: "pi", title: "Shiver", artist: "Coldplay", cover: "", track_id: "s2", liked: false,
    progress_ms: 30000, duration_ms: 200000, paused: false, volume: 60, fetched_at: Date.now(), sleep_end: 0, shuffle: false, repeat: "off", queue, queue_index: 1, seekable: true, equalizer: [0, 0, 0, 0, 0, 0] };
  const { getSaved, cspErrors, pageErrors } = await mockBackend(page, { overrides: { music } });
  const calls = await mockLibrary(page);
  const controls = () => calls.filter(([path]) => path === "/api/player/control").map(([, body]) => [body.action, body.value]);
  await page.goto("/");
  await openMenu(page);
  // Startseite zeigt, was läuft; „Musik“ öffnet den Player.
  await expect(page.locator("#homeTitle")).toHaveText("Shiver");
  await openTile(page, "player");
  await expect(page.locator("#ppTitle")).toHaveText("Shiver");
  await expect(page.locator("#queueList .track-row")).toHaveCount(4);
  await expect(page.locator("#queueList .track-row").nth(1)).toHaveClass(/is-playing/);
  await page.locator("#queueList .track-row", { hasText: "Clocks" }).click();
  await page.locator("#ppShuffle").click();
  await expect(page.locator("#ppShuffle")).toHaveAttribute("aria-pressed", "true");
  await page.locator("#ppRepeat").click();
  await expect(page.locator("#ppRepeat")).toHaveAttribute("data-mode", "all");
  await page.locator("#queueList .track-row", { hasText: "Yellow" }).getByRole("button", { name: "Aus der Warteschlange entfernen" }).click();
  await page.locator("#ppSeek").fill("500");
  await expect.poll(controls).toEqual([["jump", 3], ["shuffle", true], ["repeat", "all"], ["remove", 2], ["seek", 100]]);
  await page.locator("#ppLike").click();
  await expect.poll(() => calls.find(([path]) => path === "/api/player/like")?.[1]).toEqual({ service: "ytmusic", id: "s2", liked: true });
  await page.locator("#ppAdd").click();
  // „Liked Music“ füllt sich über das Herz und steht nicht in der Liste.
  await expect(page.locator("#ppAddList .pick")).toHaveCount(1);
  await page.locator("#ppAddList .pick").click();
  await expect.poll(() => calls.find(([path]) => path === "/api/player/add")?.[1]).toEqual({ service: "ytmusic", playlist: "PLauto", id: "s2" });
  await page.locator('#eqPresets [data-preset="bass"]').click();
  await expect.poll(() => getSaved().music.equalizer).toEqual({ preset: "bass", gains: [6, 4, 1, 0, 0, 0] });
  await expect(page.locator("#eqBands output").first()).toHaveText("+6");
  // Beim Durchsuchen bleibt unten der Mini-Player, er führt zurück zum Player.
  await page.locator("#toBrowse").click();
  await expect(visiblePage(page, "music")).toBeVisible();
  await expect(page.locator("#miniTitle")).toHaveText("Shiver");
  await page.locator("#miniOpen").click();
  await expect(visiblePage(page, "player")).toBeVisible();
  await expect(visiblePage(page, "music")).toBeHidden();
  expect(cspErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test("popups close when tapped and the menu closes with a swipe to the left", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockBackend(page);
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "display");
  await page.locator('#distSeg [data-dist="3"]').click();
  await expect(page.locator("#toast")).toBeVisible();
  await page.locator("#toastText").click();
  await expect(page.locator("#toast")).toBeHidden();
  // Auswahl als großes Blatt statt Dropdown
  await page.locator('label[for="compactSel"]').click();
  await expect(page.locator("#action")).toBeVisible();
  await page.locator("#actionBody .pick-row", { hasText: "Nach 5 Minuten" }).click();
  await expect(page.locator("#action")).toBeHidden();
  await expect(page.locator('label[for="compactSel"] .picker-value')).toHaveText("Nach 5 Minuten");
  // Uhrzeit mit Tasten statt Mini-Zeitfeld
  await page.locator('label[for="nightFrom"]').click();
  await page.locator("#actionBody .time-col").first().getByRole("button", { name: "Stunde mehr" }).click();
  await page.locator("#actionBody .button-primary").click();
  await expect(page.locator('label[for="nightFrom"] .picker-value')).toHaveText("00:00 Uhr");
  await page.locator('.page[data-panel="display"] [data-back]').click();
  await expect(visiblePage(page, "home")).toBeVisible();
  const box = await page.locator('.page[data-panel="home"] .scroll').boundingBox();
  await page.mouse.move(box.x + box.width * 0.7, box.y + 60);
  await page.mouse.down();
  for (let step = 1; step <= 8; step += 1) await page.mouse.move(box.x + box.width * (0.7 - step * 0.07), box.y + 62, { steps: 2 });
  await page.mouse.up();
  await expect(page.locator("#sheet")).toBeHidden();
});

test("gaming: missing helper is explained, then install wizard with folder choice and real steps", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const { cspErrors, pageErrors } = await mockBackend(page);
  let agent = false;
  let installed = false;
  const calls = [];
  const job = (state) => ({ kind: "install", state, error: "", steps: [
    { key: "folders", label: "Ordner vorbereiten", status: "done", detail: "config, roms" },
    { key: "pull", label: "Image laden", status: state === "running" ? "running" : "done", detail: "Downloading 120MB/410MB" },
    { key: "ready", label: "Oberfläche prüfen", status: state === "running" ? "pending" : "done", detail: "" },
  ] });
  let jobState = "running";
  const container = { name: "storchcam-gaming", state: "running", running: true, reachable: true, image: "lscr.io/linuxserver/retroarch:latest", version: "1.22.2-ls96", port: 3001, mounts: [] };
  await page.route("**/api/gaming/**", async (route) => {
    const url = new URL(route.request().url());
    calls.push([route.request().method(), url.pathname, route.request().postDataJSON?.() ?? null]);
    const reply = (json, status = 200) => route.fulfill({ status, json });
    if (url.pathname === "/api/gaming/status") {
      if (!agent) return reply({ ok: true, agent: false, install_command: "sudo ./scripts/gaming/install-agent.sh", settings: { pause_camera: false } });
      return reply({ ok: true, agent: true, docker: { ok: true, version: "27.3.1" }, container: installed ? container : null, candidates: [], job: null, setup: { folder: "/home/pi/GAMING" }, settings: { pause_camera: false } });
    }
    if (url.pathname === "/api/gaming/folders" && route.request().method() === "GET") {
      const path = url.searchParams.get("path");
      if (!path) return reply({ path: "", roots: [{ path: "/home/pi", free_bytes: 30e9, writable: true }], entries: [] });
      return reply({ path, parent: "", free_bytes: 30e9, writable: true, gaming: false, roots: ["/home/pi"], entries: [{ name: "GAMING", path: "/home/pi/GAMING", writable: true, gaming: false }] });
    }
    if (url.pathname === "/api/gaming/ports") return reply({ port: 3001, free: true });
    if (url.pathname === "/api/gaming/install") return reply(job("running"));
    if (url.pathname === "/api/gaming/job") { const answer = { job: job(jobState) }; if (jobState === "done") installed = true; jobState = "done"; return reply(answer); }
    if (url.pathname === "/api/gaming/access") return reply({ ok: true, url: "https://raspberrypi.local:3001/", user: "spieler", password: "geheim123", running: true, qr: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E" });
    if (url.pathname === "/api/gaming/roms") return reply({ folder: "/home/pi/GAMING/roms", files: [], extensions: [".nes"] });
    if (url.pathname === "/api/gaming/control") return reply({ ok: true, container: { ...container, running: false, state: "exited", reachable: false } });
    return reply({ ok: false, error: "unbekannt" }, 404);
  });
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "gaming");
  await expect(page.locator('[data-gm="agent"]')).toBeVisible();
  await expect(page.locator("#gmAgentCmd")).toHaveText("sudo ./scripts/gaming/install-agent.sh");
  agent = true;
  await page.locator('[data-gm="agent"] [data-gm-reload]').click();
  await expect(page.getByText("Noch kein Gaming-System eingerichtet")).toBeVisible();
  await page.locator("#gmStart").click();
  await page.locator("#gmFolders .row-link", { hasText: "/home/pi" }).click();
  await page.locator("#gmFolders .row-link", { hasText: "GAMING" }).click();
  await page.locator("#gmChoose").click();
  await expect(page.locator("#gmReviewPath")).toHaveText("/home/pi/GAMING");
  await expect(page.locator("#gmPortInfo")).toHaveText("frei");
  await page.locator("#gmInstall").click();
  // Installieren verändert den Pi: erst nach Bestätigung
  expect(calls.some(([, path]) => path === "/api/gaming/install")).toBe(false);
  await page.locator("#actionBody .button-primary").click();
  await expect.poll(() => calls.find(([, path]) => path === "/api/gaming/install")?.[2]).toEqual({ folder: "/home/pi/GAMING", port: 3001, confirm: true });
  await expect(page.locator("#gmSteps .gm-step.is-running")).toHaveCount(1);
  await expect(page.locator("#gmStateTitle")).toHaveText("Gaming läuft", { timeout: 8000 });
  await expect(page.locator("#gmUrl")).toHaveText("https://raspberrypi.local:3001/");
  // Am Pi-Bildschirm öffnet sich kein neues Fenster (der Kiosk hätte keinen Weg zurück).
  await expect(page.locator("#gmOpen")).toHaveText("Auf Handy oder Laptop öffnen");
  const popups = [];
  page.on("popup", (popup) => popups.push(popup));
  await page.locator("#gmOpen").click();
  await page.locator("#gmUrl").click();
  await page.waitForTimeout(300);
  expect(popups).toEqual([]);
  await expect(page.locator("#gmRoms")).toContainText("Noch keine Spiele vorhanden. Füge deine ROM-Dateien hinzu.");
  await page.locator("#gmToggle").click();
  await expect.poll(() => calls.find(([, path]) => path === "/api/gaming/control")?.[2]).toEqual({ action: "stop" });
  await expect(page.locator("#gmStateTitle")).toHaveText("Gaming ist gestoppt");
  expect(cspErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test("gaming on the Pi screen: RetroArch starts natively, controllers are paired over Bluetooth", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const backend = await mockBackend(page);
  let screen = { installed: false, cores: 0, display: true, running: false, started_at: 0, last_exit: null, install_command: "sudo ./scripts/gaming/install-agent.sh --screen" };
  const posts = [];
  const container = { name: "storchcam-gaming", state: "running", running: true, reachable: true, image: "lscr.io/linuxserver/retroarch:latest", version: "1.22.2-ls96", port: 3001, mounts: [] };
  await page.route("**/api/gaming/**", async (route) => {
    const url = new URL(route.request().url());
    const reply = (json) => route.fulfill({ json });
    if (url.pathname === "/api/gaming/status") return reply({ ok: true, agent: "2", docker: { ok: true, version: "27.3.1" }, container, candidates: [], job: null, setup: { folder: "/home/pi/GAMING" }, settings: { pause_camera: false }, screen });
    if (url.pathname === "/api/gaming/access") return reply({ ok: true, url: "https://192.168.178.40:3001/", urls: ["https://192.168.178.40:3001/"], user: "spieler", password: "geheim123", running: true, qr: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E", on_pi: true });
    if (url.pathname === "/api/gaming/roms") return reply({ folder: "/home/pi/GAMING/roms", files: [], extensions: [".nes"] });
    if (url.pathname === "/api/gaming/screen") {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        posts.push(body);
        screen = { ...screen, running: body.action === "start", last_exit: body.action === "stop" ? { code: -15, seconds: 60, error: "" } : null };
      }
      return reply(screen);
    }
    return route.fulfill({ status: 404, json: { ok: false } });
  });
  await page.goto("/");
  await openMenu(page);
  await openTile(page, "gaming");
  // Ohne RetroArch auf dem Pi: der Befehl zum einmaligen Installieren, kein Startknopf.
  await expect(page.locator("#gmScreenCmd")).toHaveText("sudo ./scripts/gaming/install-agent.sh --screen");
  await expect(page.locator("#gmScreen")).toBeHidden();
  // Installiert: Start am Bildschirm schließt das Menü, damit nach dem Spielen die Kamera erscheint.
  screen = { ...screen, installed: true, cores: 9 };
  await page.locator("[data-panel='gaming'] [data-back]").click();
  await openTile(page, "gaming");
  await expect(page.locator("#gmScreenCmd")).toBeHidden();
  await expect(page.locator("#gmScreenSub")).toContainText("9 Emulator-Kerne");
  await expect(page.locator("#gmScreen")).toHaveText("Am Pi-Bildschirm spielen");
  await page.locator("#gmScreen").click();
  await expect.poll(() => posts).toEqual([{ action: "start" }]);
  await expect(page.locator("#sheet")).toBeHidden();
  // Später wieder im Menü: beenden mit Rückfrage.
  await openMenu(page);
  await openTile(page, "gaming");
  await expect(page.locator("#gmScreen")).toHaveText("Am Pi-Bildschirm beenden");
  await page.locator("#gmScreen").click();
  await page.locator("#action .button-primary").click();
  await expect.poll(() => posts).toEqual([{ action: "start" }, { action: "stop" }]);
  await expect(page.locator("#gmScreen")).toHaveText("Am Pi-Bildschirm spielen");
  // Controller koppeln führt zur Bluetooth-Liste.
  await page.locator("#gmPads").click();
  await expect(visiblePage(page, "accounts")).toBeVisible();
  await expect(page.locator("#btScan")).toHaveText("Geräte suchen");
  expect(backend.cspErrors).toEqual([]);
  expect(backend.pageErrors).toEqual([]);
});
