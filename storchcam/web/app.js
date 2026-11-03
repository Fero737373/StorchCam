"use strict";

/* StorchCam Live-Cam-Kiosk.
   Kamera im eigenen Player, Infoleiste mit Uhr, Wetter und Abfahrten, Songkarte oben rechts und ein Menü als
   Seitenpanel. Änderungen im Menü werden sofort gespeichert und lassen sich 10 Sekunden lang rückgängig machen.
   Die CSP verbietet Inline-Styles im HTML; Farben kommen über Klassen und CSS-Variablen, Maße über CSSOM. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const byId = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, className = "", text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function icon(id, className = "i") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function capitalize(text) {
  const value = String(text || "");
  return value ? value[0].toLocaleUpperCase("de-DE") + value.slice(1) : value;
}

function hue(text) {
  let sum = 0;
  for (const character of String(text || "")) sum = (sum * 31 + character.codePointAt(0)) % 360;
  return sum;
}

function initials(name) {
  const words = String(name || "?").replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 3).toLocaleUpperCase("de-DE");
  return (words[0][0] + (/\d/.test(words[1]) ? words[1].slice(0, 2) : words[1][0])).toLocaleUpperCase("de-DE");
}

/* ---------- Zustand ---------- */
let token = sessionStorage.getItem("storchcam-admin-token") || "";
let runtime = null;          // öffentliche Laufzeitkonfiguration aus /api/state
let lastState = null;        // letzter Zustand vom Server
let currentConfig = null;    // vollständige Konfiguration (nur mit Menüzugang)
const meta = { youtubeKeySet: false, spotifyConnected: false, ytConnected: false, remote: false, pinLogin: true, backlight: false };
let backendDown = false;
let booted = false;

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { error: `Ungültige Serverantwort (${response.status})` };
  }
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    if (response.status === 401 && token) {
      token = "";
      sessionStorage.removeItem("storchcam-admin-token");
    }
    throw error;
  }
  return payload;
}

const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });

function setStatus(target, text, kind = "") {
  if (!target) return;
  target.textContent = text;
  target.classList.remove("error", "success");
  if (kind) target.classList.add(kind);
}

/* ---------- Zeit in der Zeitzone des Orts ---------- */
const tz = () => runtime?.timezone || "Europe/Berlin";
const FORMATS = {};
function formatter(key, options) {
  const id = `${key}|${tz()}`;
  FORMATS[id] = FORMATS[id] || new Intl.DateTimeFormat("de-DE", { ...options, timeZone: tz() });
  return FORMATS[id];
}
const fmtTime = (value) => formatter("time", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
const fmtDate = (value) => formatter("date", { weekday: "long", day: "numeric", month: "long" }).format(new Date(value));
function hourOf(value) {
  const part = formatter("hour", { hour: "numeric", hourCycle: "h23" }).formatToParts(new Date(value)).find((item) => item.type === "hour");
  return Number(part?.value ?? 0);
}
function minutesOfDay(value = Date.now()) {
  const parts = formatter("hm", { hour: "numeric", minute: "numeric", hourCycle: "h23" }).formatToParts(new Date(value));
  const get = (type) => Number(parts.find((item) => item.type === type)?.value ?? 0);
  return get("hour") * 60 + get("minute");
}
function ago(iso) {
  const stamp = Date.parse(iso || "");
  if (!Number.isFinite(stamp)) return "–";
  const seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
  if (seconds < 60) return `vor ${seconds} Sek.`;
  if (seconds < 3600) return `vor ${Math.round(seconds / 60)} Min.`;
  return `vor ${Math.floor(seconds / 3600)} Std. ${Math.round((seconds % 3600) / 60)} Min.`;
}
function every(seconds) {
  if (!seconds) return "–";
  return seconds < 60 ? `${seconds} Sek.` : seconds < 3600 ? `${Math.round(seconds / 60)} Min.` : `${Math.round(seconds / 3600)} Std.`;
}
function isDaytime() {
  const weather = lastState?.weather;
  if (weather?.ok && typeof weather.is_day === "boolean") return weather.is_day;
  const hour = hourOf(Date.now());
  return hour >= 7 && hour < 19;
}

/* ---------- Hinweis mit Rückgängig ---------- */
let toastTimer = null;
function toast(text, undo) {
  const box = byId("toast");
  byId("toastText").textContent = text;
  const button = byId("toastUndo");
  button.hidden = !undo;
  button.onclick = undo ? async () => { box.hidden = true; await undo(); } : null;
  const wasHidden = box.hidden;
  box.hidden = false;
  if (wasHidden && !reduceMotion) box.animate([{ opacity: 0, transform: "translate(-50%, 16px) scale(.96)" }, { opacity: 1, transform: "translate(-50%, 0)" }], { duration: 380, easing: "cubic-bezier(.34,1.56,.64,1)" });
  const check = byId("toastCheck");
  check.classList.remove("draw");
  void check.getBoundingClientRect();
  check.classList.add("draw");
  const duration = undo ? 10000 : 3500;
  const bar = byId("toastBar");
  bar.getAnimations().forEach((animation) => animation.cancel());
  if (!reduceMotion) bar.animate([{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }], { duration, easing: "linear", fill: "forwards" });
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, duration);
}

/* Jede Änderung im Menü: sofort speichern, Anzeige anpassen, 10 Sekunden „Rückgängig“ anbieten. */
async function saveConfig(mutate, label, { undo = true, quiet = false } = {}) {
  if (!currentConfig) await loadConfig();
  const before = clone(currentConfig);
  const next = clone(currentConfig);
  mutate(next);
  try {
    const result = await post("/api/config/save", { config: next });
    adoptConfig(result.config);
  } catch (error) {
    toast(`Nicht gespeichert: ${error.message}`);
    syncSheet();
    return false;
  }
  byId("saveFoot").textContent = `Gespeichert um ${fmtTime(Date.now())}`;
  if (!quiet) {
    toast(`Gespeichert · ${label}`, undo ? async () => {
      try {
        const result = await post("/api/config/save", { config: before });
        adoptConfig(result.config);
        toast("Rückgängig gemacht");
      } catch (error) {
        toast(`Rückgängig nicht möglich: ${error.message}`);
      }
    } : null);
  }
  return true;
}

function adoptConfig(config) {
  currentConfig = config;
  applyRuntime({
    ...(runtime || {}),
    ui: config.ui,
    stream: config.stream,
    location: config.location,
    timezone: config.app?.timezone || runtime?.timezone,
    transit_leave_mode: !!config.transit?.leave_mode,
    transit_stop_count: (config.transit?.stops || []).length,
    music_source: config.music?.source,
    warnings: config.weather?.warnings,
  });
  armCompact();
  syncSheet();
  setTimeout(refreshState, 1500);
}

/* ---------- Layout, Leseabstand, Theme ---------- */
const DISTANCE_SCALE = { 1: 0.86, 2: 1, 3: 1.2 };
function computeLayout() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  const portrait = height > width * 1.05 || width < 640;
  const base = portrait ? Math.min(1.1, Math.max(0.7, width / 540)) : Math.min(1.55, Math.max(0.74, Math.min(width / 1280, height / 720)));
  let scale = base * (DISTANCE_SCALE[runtime?.ui?.distance] || 1);
  if (!portrait) scale = Math.min(scale, width / 1060);
  document.body.style.setProperty("--s", scale.toFixed(3));
  document.body.dataset.layout = portrait ? "portrait" : "landscape";
}

function effectiveTheme() {
  const theme = runtime?.ui?.theme || "dark";
  return theme === "auto" ? (isDaytime() ? "light" : "dark") : theme;
}

function applyTheme() {
  const theme = effectiveTheme();
  const sky = runtime?.ui?.sky || "auto";
  if (document.body.dataset.theme !== theme) {
    document.body.dataset.theme = theme;
    if (booted) renderClock(true);
  }
  const nextSky = sky === "auto" ? (isDaytime() ? "day" : "night") : sky;
  if (document.body.dataset.sky !== nextSky) document.body.dataset.sky = nextSky;
  const chosen = runtime?.ui?.theme || "dark";
  if (applyTheme.chosen !== chosen) {
    applyTheme.chosen = chosen;
    $$("#themeGrid .theme-card").forEach((card) => card.setAttribute("aria-pressed", String(card.dataset.theme === chosen)));
  }
}

function blocks() {
  const ui = runtime?.ui || {};
  return { clock: !!ui.clock?.enabled, weather: !!ui.weather?.enabled, transit: !!ui.transit?.enabled, system: !!ui.system?.enabled };
}
const anyBlock = () => Object.values(blocks()).some(Boolean);

function applyBlocks() {
  const shown = blocks();
  const clockBlock = byId("clockBlock");
  clockBlock.hidden = !(shown.clock || shown.system);
  byId("clock").hidden = !shown.clock;
  byId("date").hidden = !shown.clock;
  byId("sys").hidden = !shown.system;
  byId("weatherBlock").hidden = !shown.weather;
  byId("transitBlock").hidden = !shown.transit;
  const separators = $$(".ib-sep");
  separators[0].hidden = clockBlock.hidden || (!shown.weather && !shown.transit);
  separators[1].hidden = !shown.transit || (!shown.weather && clockBlock.hidden);
  const bar = byId("infobar");
  if (document.body.dataset.layout !== "portrait") {
    const columns = [];
    if (!clockBlock.hidden) columns.push("minmax(calc(212px * var(--s)), max-content)");
    if (!separators[0].hidden) columns.push("1px");
    if (shown.weather) columns.push("minmax(0, 1fr)");
    if (!separators[1].hidden) columns.push("1px");
    if (shown.transit) columns.push(shown.weather ? "calc(430px * var(--s))" : "minmax(calc(430px * var(--s)), 1fr)");
    bar.style.gridTemplateColumns = columns.join(" ");
  } else {
    bar.style.gridTemplateColumns = "";
  }
  setBar(barMode, true);
}

function layoutDock() {
  requestAnimationFrame(() => document.body.style.setProperty("--dock-h", `${byId("dock").offsetHeight}px`));
}

/* ---------- Uhr und Statuszeile ---------- */
let lastMinute = "";
function flapText(node, text) {
  if (document.body.dataset.theme !== "flap") {
    if (node.children.length || node.textContent !== text) node.textContent = text;
    return;
  }
  const old = $$(".fl", node).map((item) => item.textContent);
  if (old.length && old.join("") === text) return;
  node.replaceChildren(...[...text].map((character, index) => {
    const flap = el("span", character === ":" ? "fl fl-sep" : "fl", character);
    if (old.length && old[index] !== character && character !== ":" && !reduceMotion) flap.classList.add("flip");
    return flap;
  }));
}

function renderClock(force = false) {
  const now = Date.now();
  const time = fmtTime(now);
  if (force) lastMinute = "";
  flapText(byId("clock"), time);
  flapText(byId("nightClock"), time);
  flapText(byId("mbClock"), time);
  byId("date").textContent = fmtDate(now);
  if (lastMinute && lastMinute !== time && document.body.dataset.theme === "paper" && !reduceMotion) {
    const block = byId("clockBlock");
    block.classList.remove("eink");
    void block.offsetWidth;
    block.classList.add("eink");
  }
  lastMinute = time;
  renderStatus();
}

function sourceStates() {
  const health = lastState?.health || {};
  const names = { weather: "Wetter · Open-Meteo", boards: "Abfahrten · VRR", warnings: "Warnungen · DWD", music: "Musik", system: "System", gaming: "Gaming · Hilfsdienst" };
  const rows = Object.entries(health).map(([key, item]) => {
    const state = item.ok ? "ok" : item.last_ok ? "warn" : "bad";
    let text = item.ok ? "Aktuell" : item.detail || (state === "warn" ? "Veraltet" : "Gestört");
    if (key === "warnings" && item.ok) {
      const count = lastState?.warnings?.alerts?.length || 0;
      text = lastState?.warnings?.covered === false ? "Ort außerhalb Deutschlands" : count ? `${count} Warnung${count > 1 ? "en" : ""} aktiv` : "Keine Warnung";
    }
    return { key, name: names[key] || key, state, text, last: item.ok ? ago(item.checked_at) : item.error || ago(item.last_ok), limit: every(item.interval) };
  });
  rows.push({ key: "stream", name: "Livestream · eigener Player", ...streamHealth() });
  return rows;
}

function renderStatus() {
  const states = sourceStates();
  const worst = states.some((item) => item.state === "bad") ? "bad" : states.some((item) => item.state === "warn") ? "warn" : "ok";
  const dotClass = `dot${worst === "warn" ? " is-warn" : worst === "bad" ? " is-bad" : ""}`;
  byId("sysDot").className = dotClass;
  byId("mbDot").className = dotClass;
  const parts = [];
  if (player.reconnecting) parts.push("Kamera antwortet nicht · neuer Versuch");
  if (states.find((item) => item.key === "boards" && item.state !== "ok")) parts.push("Abfahrten gestört");
  if (states.find((item) => item.key === "weather" && item.state !== "ok")) parts.push("Wetter veraltet");
  if (player.override) parts.push("Ersatz-Stream läuft");
  if (lastState?.warnings?.alerts?.length) parts.push("DWD-Warnung aktiv");
  if (!parts.length) parts.push(backendDown ? "Lokales Backend nicht erreichbar" : "Alles aktuell");
  const system = lastState?.system;
  if (system && system.enabled !== false) {
    if (system.ram !== null && system.ram !== undefined) parts.push(`RAM ${system.ram} %`);
    if (system.temp !== null && system.temp !== undefined) parts.push(`${system.temp} °C`);
  }
  byId("sysText").textContent = parts.slice(0, 3).join(" · ");
}

/* ---------- Wetter ---------- */
const CLOUD = "M7 18.5h10.5a4 4 0 0 0 .5-7.97A6 6 0 0 0 6.4 11.1 3.7 3.7 0 0 0 7 18.5z";
const SUN_RAYS = "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4";
const SMALL_SUN = '<g class="wi-sun"><circle cx="7" cy="7" r="2.4"/><path d="M7 1.8v1.3M1.8 7h1.3M3.3 3.3l.9.9M10.7 3.3l-.9.9M3.3 10.7l.9-.9"/></g>';
const SMALL_MOON = '<path class="wi-moon" d="M10.2 8.6A3.6 3.6 0 1 1 5.4 3.8a2.9 2.9 0 0 0 4.8 4.8z"/>';
const WX = {
  sun: `<g class="wi-sun"><circle cx="12" cy="12" r="4"/><path d="${SUN_RAYS}"/></g>`,
  moon: '<path class="wi-moon" d="M19 14.6A7.5 7.5 0 1 1 9.4 5a6 6 0 0 0 9.6 9.6z"/>',
  partly: `${SMALL_SUN}<path class="wi-cloud" transform="translate(1.5 2)" d="${CLOUD}"/>`,
  "partly-night": `${SMALL_MOON}<path class="wi-cloud" transform="translate(1.5 2)" d="${CLOUD}"/>`,
  cloudy: `<path class="wi-cloud" d="${CLOUD}"/>`,
  fog: `<path class="wi-cloud" transform="translate(0 -4)" d="${CLOUD}"/><path class="wi-drop" d="M4.5 18.5h15M6.5 21.5h11"/>`,
  rain: `<path class="wi-cloud" transform="translate(0 -4)" d="${CLOUD}"/><path class="wi-drop" d="M8.5 17.5l-1 3M12.5 17.5l-1 3M16.5 17.5l-1 3"/>`,
  showers: `${SMALL_SUN}<path class="wi-cloud" transform="translate(1.5 1.5)" d="${CLOUD}"/><path class="wi-drop" d="M11 21l-.7 2.2M15.5 21l-.7 2.2"/>`,
  "showers-night": `${SMALL_MOON}<path class="wi-cloud" transform="translate(1.5 1.5)" d="${CLOUD}"/><path class="wi-drop" d="M11 21l-.7 2.2M15.5 21l-.7 2.2"/>`,
  snow: `<path class="wi-cloud" transform="translate(0 -4)" d="${CLOUD}"/><path class="wi-drop" d="M8.5 18.5h.01M12 20.5h.01M15.5 18.5h.01M10 22h.01M14 22h.01"/>`,
  thunder: `<path class="wi-cloud" transform="translate(0 -4)" d="${CLOUD}"/><path class="wi-bolt" d="M12.8 15.5l-2.6 3.8h3.4l-2.4 3.7"/>`,
};
function weatherKind(code, day = true) {
  if (code === 0 || code === 1) return day ? "sun" : "moon";
  if (code === 2) return day ? "partly" : "partly-night";
  if (code === 45 || code === 48) return "fog";
  if (code >= 51 && code <= 67) return "rain";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
  if (code >= 80 && code <= 82) return day ? "showers" : "showers-night";
  if (code >= 95 && code <= 99) return "thunder";
  return "cloudy";
}
const wxSvg = (kind) => `<svg viewBox="0 0 24 24" aria-hidden="true">${WX[kind] || WX.cloudy}</svg>`;

function hoursOf(weather) {
  return (weather?.hourly || []).filter((hour) => hour.temp !== null && hour.temp !== undefined);
}

function stripSummaryText(weather, hours) {
  const threshold = weather.rain_threshold || 45;
  if (weather.stale) return `Stand ${weather.updated_at ? fmtTime(weather.updated_at) : "–"} · keine Verbindung`;
  if (!hours.length) return "";
  const wet = hours.find((hour) => hour.probability >= threshold);
  if (wet === hours[0]) return `Regen jetzt · ${hours[0].probability} %`;
  if (wet) return `Regen ab ${wet.hour} Uhr · ${wet.probability} %`;
  return `kein Regen bis ${hours.at(-1).hour} Uhr`;
}

function renderWeather() {
  const weather = lastState?.weather;
  const block = byId("weatherBlock");
  if (!weather?.ok) {
    byId("weatherIcon").innerHTML = wxSvg("cloudy");
    byId("weatherTemp").textContent = "–°";
    byId("weatherCond").textContent = weather?.error ? "Wetter nicht erreichbar" : "Wetter wird geladen";
    byId("weatherDetails").textContent = weather?.error ? "neuer Versuch läuft" : "";
    byId("stripSummary").textContent = "";
    byId("strip").replaceChildren();
    block.classList.add("is-stale");
    renderMini();
    return;
  }
  byId("weatherIcon").innerHTML = wxSvg(weatherKind(weather.code, weather.is_day));
  const temperature = byId("weatherTemp");
  const text = `${weather.temp}°`;
  if (temperature.textContent !== text) {
    temperature.textContent = text;
    if (booted && !reduceMotion) temperature.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 500 });
  }
  byId("weatherCond").textContent = capitalize(weather.condition);
  const details = byId("weatherDetails");
  details.replaceChildren(el("span", "", `gefühlt ${weather.feels}° · Wind ${weather.wind} km/h`), el("span", "wd-hum", ` · Luftfeuchte ${weather.hum} %`));
  block.classList.toggle("is-stale", !!weather.stale);
  renderStrip(weather);
  renderMini();
}

function sunMarks(weather) {
  const marks = [];
  (weather.sun || []).forEach((day) => {
    [[day.rise, "↑", "Sonnenaufgang"], [day.set, "↓", "Sonnenuntergang"]].forEach(([iso, arrow, name]) => {
      const stamp = Date.parse(iso || "");
      if (Number.isFinite(stamp)) marks.push({ stamp, arrow, name, label: fmtTime(stamp) });
    });
  });
  return marks;
}

function markInHour(marks, hourIso) {
  const start = Date.parse(hourIso);
  return marks.find((mark) => mark.stamp >= start && mark.stamp < start + 3600e3);
}

/* Wetterstreifen: Temperaturkurve über Regenbalken, gestrichelte Regenschwelle und Sonnenzeiten. */
function renderStrip(weather) {
  const svg = byId("strip");
  const width = Math.max(120, Math.round(svg.getBoundingClientRect().width || 300));
  const scale = parseFloat(getComputedStyle(document.body).getPropertyValue("--s")) || 1;
  const count = width >= 520 ? 12 : width >= 330 ? 8 : 6;
  const hours = hoursOf(weather).slice(0, count);
  byId("stripLabel").textContent = `Nächste ${hours.length || count} Stunden`;
  byId("stripSummary").textContent = stripSummaryText(weather, hours);
  if (hours.length < 2) { svg.replaceChildren(); return; }
  const threshold = weather.rain_threshold || 45;
  const height = Math.round(70 * scale);
  const font = Math.max(10.5, 12 * scale * 1.05);
  const tempTop = 4;
  const tempBottom = Math.round(height * 0.34);
  const barTop = Math.round(height * 0.42);
  const barBottom = height - Math.round(font + 4);
  const cell = width / hours.length;
  const gap = Math.max(3, cell * 0.12);
  const temps = hours.map((hour) => hour.temp);
  const low = Math.min(...temps);
  const high = Math.max(...temps);
  const ty = (value) => (high === low ? (tempTop + tempBottom) / 2 : tempBottom - ((value - low) / (high - low)) * (tempBottom - tempTop - font * 0.2));
  const barHeight = barBottom - barTop;
  const peak = hours.reduce((best, hour) => (hour.probability > best.probability ? hour : best));
  const marks = sunMarks(weather);
  let out = "";
  hours.forEach((hour, index) => {
    const x = index * cell + gap / 2;
    const barWidth = cell - gap;
    const filled = Math.max(3, (hour.probability / 100) * barHeight);
    const wet = hour.probability >= threshold;
    out += `<rect x="${x.toFixed(1)}" y="${barTop}" width="${barWidth.toFixed(1)}" height="${barHeight}" rx="4" fill="var(--bar-track)"/>`;
    out += `<rect x="${x.toFixed(1)}" y="${(barBottom - filled).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${filled.toFixed(1)}" rx="3" fill="${wet ? "var(--bar-wet)" : "var(--bar-dry)"}"/>`;
    if (hour === peak && hour.probability >= 30 && filled > font + 2) out += `<text x="${(x + barWidth / 2).toFixed(1)}" y="${(barBottom - filled + font).toFixed(1)}" text-anchor="middle" font-size="${font * 0.9}" font-weight="700" fill="${wet ? "var(--on-wet)" : "var(--text)"}">${hour.probability}</text>`;
    let label = index === 0 ? "jetzt" : String(hour.hour);
    const mark = index > 0 ? markInHour(marks, hour.time) : null;
    if (mark) {
      const sx = index * cell + ((mark.stamp - Date.parse(hour.time)) / 3600e3) * cell;
      label = `${mark.arrow}${mark.label}`;
      out += `<line x1="${sx.toFixed(1)}" x2="${sx.toFixed(1)}" y1="${tempTop + 2}" y2="${barBottom}" stroke="var(--hint)" stroke-width="1.2" stroke-dasharray="3 3"/>`;
    }
    out += `<text x="${(index * cell + cell / 2).toFixed(1)}" y="${height - 2}" text-anchor="middle" font-size="${font}" font-weight="${index === 0 || mark ? 600 : 400}" fill="${mark ? "var(--hint)" : index === 0 ? "var(--text)" : "var(--faint)"}">${label}</text>`;
  });
  const line = barBottom - (threshold / 100) * barHeight;
  out += `<line x1="0" x2="${width}" y1="${line.toFixed(1)}" y2="${line.toFixed(1)}" stroke="var(--muted)" stroke-opacity="0.7" stroke-width="1.2" stroke-dasharray="4 4"/>`;
  const points = hours.map((hour, index) => `${(index * cell + cell / 2).toFixed(1)},${ty(hour.temp).toFixed(1)}`).join(" ");
  out += `<polyline points="${points}" fill="none" stroke="var(--temp-line)" stroke-width="${Math.max(1.6, 2 * scale)}" stroke-linejoin="round" stroke-linecap="round"/>`;
  const labelled = new Set([0, temps.indexOf(low), temps.indexOf(high)]);
  labelled.forEach((index) => {
    const x = index * cell + cell / 2;
    const y = ty(hours[index].temp);
    out += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${2.6 * scale}" fill="var(--temp-line)"/>`;
    const anchor = index === 0 ? "start" : index === hours.length - 1 ? "end" : "middle";
    const dx = index === 0 ? 6 : index === hours.length - 1 ? -6 : 0;
    out += `<text x="${(x + dx).toFixed(1)}" y="${(y < tempTop + font ? y + font + 2 : y - 4).toFixed(1)}" text-anchor="${anchor}" font-size="${font * 0.95}" font-weight="600" fill="var(--temp-line)">${hours[index].temp}°</text>`;
  });
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("height", String(height));
  svg.innerHTML = out;
}

function renderWeatherChart() {
  const weather = lastState?.weather;
  const svg = byId("wchart");
  const hours = hoursOf(weather).slice(0, 24);
  byId("dwTitle").textContent = `Wetter · ${runtime?.location?.label || ""} · nächste 24 Stunden`;
  if (hours.length < 2) { svg.replaceChildren(); return; }
  const threshold = weather.rain_threshold || 45;
  byId("dwSub").textContent = `Temperatur, Regenwahrscheinlichkeit, Wind · gestrichelt: Regenschwelle ${threshold} % · schließt sich nach 20 Sekunden`;
  const width = Math.max(320, Math.round(svg.getBoundingClientRect().width || 800));
  const height = 250;
  const pad = 8;
  const cell = (width - 2 * pad) / hours.length;
  const iconY = 6, tTop = 72, tBottom = 128, bTop = 146, bBottom = 200, hourY = 218, windY = 240;
  const temps = hours.map((hour) => hour.temp);
  const low = Math.min(...temps), high = Math.max(...temps);
  const ty = (value) => tBottom - ((value - low) / Math.max(1, high - low)) * (tBottom - tTop);
  const marks = sunMarks(weather);
  let out = "";
  hours.forEach((hour, index) => { if (!hour.is_day) out += `<rect x="${(pad + index * cell).toFixed(1)}" y="0" width="${(cell + 0.5).toFixed(1)}" height="${bBottom}" fill="var(--bar-track)"/>`; });
  hours.forEach((hour, index) => {
    const x = pad + index * cell;
    const filled = Math.max(2, (hour.probability / 100) * (bBottom - bTop));
    out += `<rect x="${(x + cell * 0.15).toFixed(1)}" y="${(bBottom - filled).toFixed(1)}" width="${(cell * 0.7).toFixed(1)}" height="${filled.toFixed(1)}" rx="2" fill="${hour.probability >= threshold ? "var(--bar-wet)" : "var(--bar-dry)"}"/>`;
    if (index % 3 === 0) {
      const cx = x + cell / 2;
      out += `<g transform="translate(${(cx - 13).toFixed(1)} ${iconY}) scale(1.08)" fill="none" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${WX[weatherKind(hour.code, hour.is_day)]}</g>`;
      out += `<text x="${cx.toFixed(1)}" y="${hourY}" text-anchor="middle" font-size="12" fill="var(--faint)">${index === 0 ? "jetzt" : `${hour.hour} Uhr`}</text>`;
      out += `<text x="${cx.toFixed(1)}" y="${windY}" text-anchor="middle" font-size="12" fill="var(--muted)">${hour.wind} km/h</text>`;
      out += `<text x="${cx.toFixed(1)}" y="${(ty(hour.temp) - 9).toFixed(1)}" text-anchor="middle" font-size="13" font-weight="600" fill="var(--text)">${hour.temp}°</text>`;
      if (hour.probability >= 20) out += `<text x="${cx.toFixed(1)}" y="${(bBottom - filled - 4).toFixed(1)}" text-anchor="middle" font-size="11" fill="var(--muted)">${hour.probability} %</text>`;
    }
    const mark = markInHour(marks, hour.time);
    if (mark) {
      const sx = x + ((mark.stamp - Date.parse(hour.time)) / 3600e3) * cell;
      out += `<line x1="${sx.toFixed(1)}" x2="${sx.toFixed(1)}" y1="38" y2="${bBottom}" stroke="var(--hint)" stroke-dasharray="3 3"/><text x="${(sx + 5).toFixed(1)}" y="50" font-size="11.5" font-weight="600" fill="var(--hint)">${mark.name} ${mark.label}</text>`;
    }
  });
  const line = bBottom - (threshold / 100) * (bBottom - bTop);
  out += `<line x1="${pad}" x2="${width - pad}" y1="${line}" y2="${line}" stroke="var(--muted)" stroke-opacity="0.6" stroke-dasharray="4 4"/>`;
  out += `<polyline points="${hours.map((hour, index) => `${(pad + index * cell + cell / 2).toFixed(1)},${ty(hour.temp).toFixed(1)}`).join(" ")}" fill="none" stroke="var(--temp-line)" stroke-width="2.4" stroke-linejoin="round"/>`;
  hours.forEach((hour, index) => { if (index % 3 === 0) out += `<circle cx="${(pad + index * cell + cell / 2).toFixed(1)}" cy="${ty(hour.temp).toFixed(1)}" r="3.2" fill="var(--temp-line)"/>`; });
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("height", String(height));
  svg.innerHTML = out;
}

/* ---------- Abfahrten mit Losgeh-Zeit ---------- */
const LINE_ICONS = { tram: "i-tram", night: "i-moon", train: "i-train", bus: "i-bus" };

function departures() {
  const list = [];
  (lastState?.boards || []).forEach((board, boardIndex) => {
    if (!board.ok) return;
    const checked = Date.parse(board.checked_at || "") || Date.now();
    (board.rows || []).forEach((row) => {
      const at = row.at ? row.at * 1000 : checked + (Number(row.minutes) || 0) * 60e3;
      list.push({
        key: `${board.station_id}|${row.line}|${row.target}|${row.planned || Math.round(at / 60e3)}`,
        line: row.line, type: row.type || "bus", target: row.target, stop: board.title, walk: Number(board.walk_minutes || 0),
        at, delay: Number(row.delay || 0), boardIndex,
      });
    });
  });
  return list.filter((item) => item.at > Date.now() - 30e3).sort((a, b) => a.at - b.at);
}

function leaveMode() {
  return runtime?.transit_leave_mode !== false;
}

function depState(item, now = Date.now()) {
  const minutes = (item.at - now) / 60e3;
  const leave = minutes - item.walk;
  if (!leaveMode() || !item.walk) return { kind: "plain", minutes, leave };
  if (leave < -0.1) return { kind: "late", minutes, leave };
  if (leave < 1) return { kind: "go", minutes, leave };
  return { kind: "leave", minutes, leave };
}

function badge(item) {
  const node = el("span", `line-badge${item.type === "bus" ? "" : ` t-${item.type}`}`);
  node.append(icon(LINE_ICONS[item.type] || "i-bus"), document.createTextNode(item.line));
  return node;
}

function timeBlock(item, state) {
  const block = el("span", "dep-time");
  if (state.kind === "go") { block.append(el("span", "dep-go", "Jetzt los")); return block; }
  if (state.kind === "late") { block.append(el("span", "dep-late", `fährt in ${Math.max(0, Math.round(state.minutes))} min`)); return block; }
  const value = Math.max(0, Math.round(state.kind === "plain" ? state.minutes : state.leave));
  if (state.kind === "plain" && value <= 0) { block.append(el("span", "dep-go", "jetzt")); return block; }
  if (value >= 60) {
    if (state.kind === "leave") block.append(el("span", "leave-label", "los um"));
    block.append(el("span", "dep-clock", fmtTime(state.kind === "plain" ? item.at : item.at - item.walk * 60e3)));
    return block;
  }
  if (state.kind === "leave") block.append(el("span", "leave-label", "los in"));
  block.append(el("span", "dep-num", String(value)), el("span", "dep-unit", "min"));
  return block;
}

function delayChip(item) {
  const chip = el("span", `delay${item.delay >= 5 ? " is-big" : ""}`, `+${item.delay}`);
  chip.title = `${item.delay} Minuten später als geplant`;
  return chip;
}

function fillRow(row, item, showStop) {
  const state = depState(item);
  row.className = `departure${state.kind === "go" ? " is-go" : state.kind === "late" ? " is-late" : ""}`;
  const oldNumber = $(".dep-num", row)?.textContent;
  const main = el("span", "dep-main");
  main.append(el("span", "dep-target", item.target));
  // Verspätung direkt hinter der Abfahrtszeit; der lange Haltestellenname wird bei Platzmangel gekürzt.
  const sub = el("span", "dep-sub");
  sub.append(document.createTextNode(`fährt ${fmtTime(item.at)}`));
  if (item.delay > 0) sub.append(delayChip(item));
  if (showStop) sub.append(el("span", "dep-stop", `· ab ${item.stop}`));
  main.append(sub);
  row.replaceChildren(badge(item), main, timeBlock(item, state));
  const number = $(".dep-num", row);
  if (number && document.body.dataset.theme === "flap") {
    const text = number.textContent;
    const previous = (oldNumber || "").slice(-text.length).padStart(text.length);
    number.replaceChildren(...[...text].map((character, index) => {
      const flap = el("span", "fl", character);
      if (oldNumber && previous.at(index) !== character && !reduceMotion) flap.classList.add("flip");
      return flap;
    }));
  } else if (oldNumber && number && oldNumber !== number.textContent && !reduceMotion) {
    number.animate([{ transform: "translateY(-45%)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 380, easing: "cubic-bezier(.2,.8,.2,1)" });
  }
}

function errorRow(board) {
  const row = el("div", "departure is-error");
  row.dataset.key = `err-${board.station_id || board.title}`;
  const symbol = el("span", "dep-icon");
  symbol.append(icon("i-alert"));
  const main = el("span", "dep-main");
  main.append(el("span", "dep-error", `${board.title} · keine Daten`), el("span", "dep-sub", `Stand ${board.checked_at ? fmtTime(board.checked_at) : "–"} · neuer Versuch läuft`));
  row.append(symbol, main);
  return row;
}

function messageRow(key, title, text) {
  const row = el("div", "departure is-empty");
  row.dataset.key = key;
  const main = el("span", "dep-main");
  main.append(el("span", "dep-target", title), el("span", "dep-sub", text));
  row.append(main);
  return row;
}

function maxRows() {
  return document.body.dataset.layout === "portrait" ? 3 : window.innerWidth < 760 ? 2 : 3;
}

function renderBoards() {
  const box = byId("boards");
  const boards = lastState?.boards || [];
  const failed = boards.filter((board) => !board.ok);
  const all = departures();
  const items = all.slice(0, Math.max(1, maxRows() - Math.min(1, failed.length)));
  const stops = new Set(items.map((item) => item.stop));
  const multiple = stops.size > 1;
  const leaving = leaveMode() && items.some((item) => item.walk > 0);
  byId("trTitle").textContent = leaving ? "Losgehen" : "Abfahrten";
  byId("trStation").textContent = multiple || !items.length ? "" : `ab ${[...stops][0]}`;
  const wanted = [];
  failed.slice(0, 1).forEach((board) => wanted.push({ key: `err-${board.station_id || board.title}`, error: board }));
  items.forEach((item) => wanted.push({ key: item.key, item }));
  if (!wanted.length) {
    const configured = Number(runtime?.transit_stop_count || 0);
    wanted.push(configured
      ? { key: "msg-none", message: ["Gerade keine Abfahrten", "Die Tafel aktualisiert sich jede Minute"] }
      : { key: "msg-setup", message: ["Keine Haltestelle eingerichtet", "Im Menü unter ÖPNV hinzufügen"] });
  }
  const existing = new Map($$(".departure", box).filter((row) => !row.dataset.leaving).map((row) => [row.dataset.key, row]));
  existing.forEach((row, key) => {
    if (wanted.some((entry) => entry.key === key)) return;
    row.dataset.leaving = "1";
    if (reduceMotion || !booted) { row.remove(); return; }
    row.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(-16px)" }], { duration: 320, easing: "ease-in" }).finished.then(() => row.remove()).catch(() => row.remove());
  });
  wanted.forEach((entry, index) => {
    let row = existing.get(entry.key);
    const fresh = !row;
    if (fresh) {
      row = entry.error ? errorRow(entry.error) : entry.message ? messageRow(entry.key, ...entry.message) : el("div", "departure");
      row.dataset.key = entry.key;
    }
    if (entry.item) fillRow(row, entry.item, multiple);
    const anchor = box.children[index];
    if (anchor !== row) box.insertBefore(row, anchor || null);
    if (fresh && booted && !reduceMotion) row.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }], { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)" });
  });
  renderMini();
  return items.some((item) => depState(item).kind === "go");
}

function renderBoardTable() {
  const table = byId("boardTable");
  const leaving = leaveMode();
  const head = el("tr");
  ["Linie", "Ziel", "Haltestelle", "Abfahrt", leaving ? "Losgehen" : "In"].forEach((title) => head.append(el("th", "", title)));
  const thead = el("thead");
  thead.append(head);
  const body = el("tbody");
  departures().forEach((item) => {
    const state = depState(item);
    const row = el("tr", state.kind === "go" ? "is-go" : state.kind === "late" ? "is-late" : "");
    const line = el("td");
    line.append(badge(item));
    const time = el("td");
    time.append(document.createTextNode(`${fmtTime(item.at)} `));
    if (item.delay > 0) time.append(delayChip(item));
    const left = state.kind === "go" ? "Jetzt los" : state.kind === "late" ? "nicht mehr erreichbar"
      : state.kind === "plain" ? `${Math.max(0, Math.round(state.minutes))} min`
        : state.leave >= 60 ? `um ${fmtTime(item.at - item.walk * 60e3)}` : `in ${Math.round(state.leave)} min`;
    row.append(line, el("td", "target", item.target), el("td", "stop", item.walk ? `${item.stop} · ${item.walk} min Weg` : item.stop), time, el("td", state.kind === "go" ? "go" : "", left));
    body.append(row);
  });
  (lastState?.boards || []).filter((board) => !board.ok).forEach((board) => {
    const row = el("tr");
    const cell = el("td", "", `${board.title} liefert gerade keine Daten, neuer Versuch läuft.`);
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
  });
  if (!body.children.length) {
    const row = el("tr");
    const cell = el("td", "", "Keine Abfahrten. Haltestellen richtest du im Menü unter ÖPNV ein.");
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
  }
  table.replaceChildren(thead, body);
  byId("legendLeave").hidden = !leaving;
}

function renderMini() {
  const weather = lastState?.weather;
  const box = byId("mbWeather");
  box.replaceChildren();
  if (weather?.ok) {
    const symbol = el("span", "wx");
    symbol.innerHTML = wxSvg(weatherKind(weather.code, weather.is_day));
    box.append(symbol, el("b", "", `${weather.temp}°`), el("span", "", byId("stripSummary").textContent || capitalize(weather.condition)));
  }
  const departure = byId("mbDep");
  departure.replaceChildren();
  const next = departures().find((item) => depState(item).kind !== "late");
  if (next) {
    const state = depState(next);
    const when = state.kind === "go" ? "Jetzt los" : state.kind === "plain" ? `${Math.max(0, Math.round(state.minutes))} min` : `los in ${Math.round(state.leave)} min`;
    departure.append(badge(next), el("span", "", next.target), el("b", "", when));
  }
}

/* ---------- Infoleiste: voll, kompakt, ausgeblendet ---------- */
let barMode = "full";
let compactTimer = null;
let forceFullUntil = 0;

function setBar(mode, silent = false) {
  const previous = barMode;
  barMode = mode;
  document.body.dataset.bar = mode;
  byId("infobar").hidden = mode !== "full" || !anyBlock();
  byId("minibar").hidden = mode !== "compact" || !anyBlock();
  byId("barPeek").hidden = mode !== "hidden" || !anyBlock();
  document.body.classList.toggle("no-infobar", mode !== "full" || !anyBlock());
  if (booted && !silent && previous !== mode && !reduceMotion) {
    const shown = mode === "full" ? byId("infobar") : mode === "compact" ? byId("minibar") : byId("barPeek");
    if (!shown.hidden) shown.animate([{ opacity: 0, transform: "translateY(16px) scale(.985)" }, { opacity: 1, transform: "none" }], { duration: 420, easing: "cubic-bezier(.2,.9,.25,1.05)" });
  }
  if (mode === "full") requestAnimationFrame(() => { if (lastState?.weather?.ok) renderStrip(lastState.weather); });
  layoutDock();
}

function busy() {
  return !byId("sheet").hidden || $$(".detail").some((detail) => !detail.hidden) || Date.now() < forceFullUntil
    || !!lastState?.warnings?.alerts?.length || byId("wizard").open || byId("nowPlaying").classList.contains("is-open");
}

function armCompact() {
  clearTimeout(compactTimer);
  const seconds = Number(runtime?.ui?.compact_after ?? 120);
  if (!seconds) return;
  compactTimer = setTimeout(() => {
    if (busy()) { armCompact(); return; }
    if (barMode === "full") setBar("compact");
  }, seconds * 1000);
}

function expandFor(milliseconds) {
  if (nightState.active) return;
  forceFullUntil = Date.now() + milliseconds;
  if (barMode !== "full") setBar("full");
}

/* ---------- Detailansichten ---------- */
let detailTimer = null;
function openDetail(kind) {
  closeDetails(true);
  const detail = byId({ depart: "detailDepart", weather: "detailWeather", warning: "detailWarning" }[kind]);
  detail.getAnimations().forEach((animation) => animation.cancel());
  if (kind === "depart") renderBoardTable();
  if (kind === "warning") renderWarningDetail();
  detail.hidden = false;
  if (kind === "weather") renderWeatherChart();
  const bar = $(".detail-timer", detail);
  if (!reduceMotion) bar.animate([{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }], { duration: 20000, easing: "linear", fill: "forwards" });
  detailTimer = setTimeout(() => closeDetails(), 20000);
}

function closeDetails(instant = false) {
  clearTimeout(detailTimer);
  $$(".detail").forEach((detail) => {
    if (detail.hidden) return;
    if (instant || reduceMotion) { detail.hidden = true; return; }
    detail.animate([{ opacity: 1, transform: "translate(-50%, 0)" }, { opacity: 0, transform: "translate(-50%, 12px)" }], { duration: 180, easing: "cubic-bezier(.4,0,1,1)" })
      .finished.then(() => { detail.hidden = true; }).catch(() => { detail.hidden = true; });
  });
}

/* ---------- Amtliche Warnungen (DWD) ---------- */
function warningWhen(alert) {
  const onset = Date.parse(alert.onset || "");
  const expires = Date.parse(alert.expires || "");
  if (Number.isFinite(onset) && onset > Date.now()) return `ab ${fmtTime(onset)} Uhr`;
  if (Number.isFinite(expires)) return `bis ${fmtTime(expires)} Uhr`;
  return "";
}

function renderWarnings() {
  const alerts = lastState?.warnings?.alerts || [];
  const box = byId("warning");
  const had = !box.hidden;
  box.hidden = !alerts.length;
  if (!alerts.length) { if (had) layoutDock(); return; }
  const first = alerts[0];
  const text = byId("warningText");
  text.replaceChildren(document.createTextNode(`${first.headline} ${warningWhen(first)}`.trim()), el("small", "", ` · Deutscher Wetterdienst${alerts.length > 1 ? ` · +${alerts.length - 1}` : ""}`));
  box.classList.toggle("is-severe", first.level >= 3);
  if (!had) {
    layoutDock();
    if (barMode !== "full" && !nightState.active) setBar("full");
  }
}

function renderWarningDetail() {
  const alerts = lastState?.warnings?.alerts || [];
  const body = byId("dwarnBody");
  body.replaceChildren(...alerts.map((alert) => {
    const card = el("div", "card");
    card.append(el("strong", "", `${alert.headline} ${warningWhen(alert)}`.trim()));
    if (alert.description) card.append(el("p", "hint", alert.description));
    if (alert.instruction) card.append(el("p", "hint", alert.instruction));
    return card;
  }));
  byId("dwarnTitle").textContent = alerts.length > 1 ? `${alerts.length} amtliche Warnungen` : "Amtliche Warnung";
}

/* ---------- Nachtmodus ---------- */
const nightState = { active: false, wakeUntil: 0, screenOff: false };
function nightWindow() {
  const night = runtime?.ui?.night;
  if (!night?.enabled) return false;
  const toMinutes = (text) => { const [hours, minutes] = String(text).split(":").map(Number); return hours * 60 + minutes; };
  const now = minutesOfDay();
  const start = toMinutes(night.start);
  const end = toMinutes(night.end);
  return start <= end ? now >= start && now < end : now >= start || now < end;
}

function applyNight() {
  const night = runtime?.ui?.night || {};
  const active = nightWindow() && Date.now() > nightState.wakeUntil && byId("sheet").hidden;
  const overlay = byId("night");
  const screenOff = active && !!night.screen_off;
  if (active !== nightState.active) {
    nightState.active = active;
    overlay.hidden = !active;
    byId("nightText").textContent = `Nachtmodus bis ${night.end || "6:00"} · antippen weckt den Bildschirm für 5 Minuten`;
    if (active) { closeDetails(true); setBar("compact", true); }
    else if (booted) setBar("full");
  }
  overlay.classList.toggle("is-off", screenOff);
  if (screenOff !== nightState.screenOff) {
    nightState.screenOff = screenOff;
    if (!meta.remote) post("/api/display/power", { on: !screenOff }).catch(() => {});
  }
}

function wakeFromNight() {
  nightState.wakeUntil = Date.now() + 5 * 60e3;
  applyNight();
  toast("Bildschirm für 5 Minuten wach");
}

/* ---------- Eigener Videoplayer ----------
   YouTube-Livestreams und HLS laufen über hls.js, normale YouTube-Videos als Videodatei in Schleife, beides in
   einem video-Element ohne Bedienelemente über den lokalen Proxy; alles andere bleibt eingebettet. Antippen, Menü
   und Dialoge halten das Video nie an. Bei Ausfall bleibt das letzte Bild stehen; gelingt der Neuaufbau nicht,
   springt ein gemerkter Favorit ein und die Anzeige kehrt zurück, sobald die Kamera wieder läuft. */
const elements = {
  playerBox: byId("player"),
  live: byId("live"),
  video: byId("liveVideo"),
  freeze: byId("streamFreeze"),
  fade: byId("fade"),
};
const player = {
  hls: null, source: null, url: "", generation: 0, retries: 0, retryTimer: null, refreshTimer: null, checkTimer: null,
  lastTime: -1, lastProgress: Date.now(), signature: "", override: null, tried: new Set(), reconnecting: false, kind: "", primary: null,
};
const PLAYER_MAX_RETRIES = 5;
const PRIMARY_CHECK_MS = 5 * 60e3;
const titles = new Map();  // Titel/Kanal je Adresse aus Suche und Favoriten, bis der Server eigene liefert

function youtubeId(url) {
  const match = String(url || "").match(/(?:youtube(?:-nocookie)?\.com\/(?:embed\/|live\/|shorts\/|watch\?(?:.*&)?v=)|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return match ? match[1] : "";
}
const thumbnailFor = (url) => (youtubeId(url) ? `https://i.ytimg.com/vi/${youtubeId(url)}/mqdefault.jpg` : "");

function streamInfo(url) {
  const favorite = (runtime?.stream?.favorites || []).find((item) => item.url === url);
  const known = titles.get(url) || {};
  const source = player.url === url ? player.source || {} : {};
  return {
    title: source.title || known.title || favorite?.title || (youtubeId(url) ? "YouTube-Livestream" : url.replace(/^https?:\/\//, "").slice(0, 60)),
    channel: source.channel || known.channel || favorite?.channel || "",
  };
}

function videoActive() {
  return elements.playerBox.classList.contains("is-video");
}

function showReconnect(title, sub, ok = false) {
  byId("signalText").textContent = title;
  byId("signalSub").textContent = sub;
  byId("signalLost").classList.toggle("is-ok", ok);
  byId("signalLost").hidden = false;
}

function hideReconnect() {
  byId("signalLost").hidden = true;
  byId("signalLost").classList.remove("is-ok");
}

function freezeFrame() {
  const video = elements.video;
  if (!videoActive() || video.readyState < 2 || !video.videoWidth) return;
  const canvas = elements.freeze;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  try {
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.classList.add("is-on");
  } catch (_error) {
    canvas.classList.remove("is-on");
  }
}

function releaseFrame() {
  elements.freeze.classList.remove("is-on");
  elements.playerBox.classList.remove("is-frozen");
}

// Kamerawechsel blendet weich über: das alte Bild liegt kurz über dem neuen.
function crossfade() {
  const video = elements.video;
  if (reduceMotion || !videoActive() || video.readyState < 2 || !video.videoWidth) return;
  const canvas = elements.fade;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  try {
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.animate([{ opacity: 1 }, { opacity: 1, offset: 0.3 }, { opacity: 0 }], { duration: 1600, easing: "ease-in-out", fill: "forwards" });
  } catch (_error) { /* ohne Bild kein Überblenden */ }
}

function stopVideo() {
  clearTimeout(player.retryTimer);
  clearTimeout(player.refreshTimer);
  if (player.hls) player.hls.destroy();
  player.hls = null;
  player.source = null;
  elements.playerBox.classList.remove("is-video");
  elements.video.removeAttribute("src");
  releaseFrame();
  hideReconnect();
}

function setStreamView(state, text) {
  byId("streamStatusText").textContent = text;
  const overlay = byId("streamEmpty");
  const showOverlay = state === "empty" || state === "down" || state === "gaming" || (state === "loading" && !videoActive() && !elements.live.getAttribute("src"));
  overlay.hidden = !showOverlay;
  const still = state === "empty" || state === "gaming";
  byId("streamSpinner").hidden = still;
  byId("streamEmptyIcon").hidden = !still;
  byId("streamReload").hidden = state !== "down";
  byId("streamEmptyTitle").textContent = state === "empty" ? "Kein Livestream eingerichtet" : state === "down" ? "Lokales Backend nicht erreichbar" : state === "gaming" ? "Kamera pausiert" : "Livestream wird vorbereitet";
  byId("streamEmptyText").textContent = state === "empty" ? "Im Menü unter Kamera eine Kamera suchen oder eine Adresse eintragen." : state === "down" ? "Die Anzeige fragt alle 5 Sekunden automatisch erneut an." : state === "gaming" ? "Solange Gaming läuft, steht das Kamerabild. Einstellbar im Menü unter Gaming." : "";
  document.body.classList.toggle("no-video", state === "empty");
  renderStatus();
}

function playEmbed(url) {
  stopVideo();
  player.kind = "embed";
  const target = new URL(url);
  target.searchParams.set("autoplay", "1");
  target.searchParams.set("mute", runtime?.stream?.audio === "on" ? "0" : "1");
  target.searchParams.set("playsinline", "1");
  elements.live.src = "about:blank";
  requestAnimationFrame(() => { elements.live.src = target.toString(); });
  setStreamView("embedded", "Stream läuft eingebettet");
  showChip();
}

async function loadSource(url, refresh, generation) {
  let source = { kind: "embed" };
  try {
    const query = new URLSearchParams();
    if (url !== (runtime?.stream?.url || "")) query.set("url", url);
    if (refresh) query.set("refresh", "1");
    const suffix = query.toString();
    source = await api(`/api/stream/source${suffix ? `?${suffix}` : ""}`);
  } catch (_error) {
    source = { kind: "embed" };
  }
  if (generation !== player.generation) return;
  player.url = url;
  if (source.kind === "hls" && window.Hls?.isSupported()) {
    playHls(source, generation);
  } else if (source.kind === "file") {
    playFile(source, generation);
  } else if (source.kind === "embed" && source.reason && youtubeId(url) && fallbackCandidate()) {
    // YouTube meldet die Kamera als nicht erreichbar: lieber einen Favoriten zeigen als ein Fehlerbild.
    startFallback(source.reason);
  } else {
    playEmbed(url);
  }
}

function attachCommon(source) {
  freezeFrame();
  if (player.hls) player.hls.destroy();
  player.hls = null;
  elements.live.removeAttribute("src");
  elements.playerBox.classList.add("is-video");
  player.source = source;
  player.kind = source.kind;
  player.lastProgress = Date.now();
  if (source.title) titles.set(player.url, { title: source.title, channel: source.channel });
  // Titel gleich zeigen, sobald er bekannt ist, nicht erst wenn das erste Bild da ist.
  if (byId("chipTitle").textContent !== streamInfo(player.url).title) showChip();
  applyAudio();
}

function playHls(source, generation) {
  const Hls = window.Hls;
  attachCommon(source);
  const video = elements.video;
  video.loop = false;
  const hls = new Hls({
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 10,
    maxBufferLength: 30,
    backBufferLength: 30,
    capLevelToPlayerSize: true,
    manifestLoadingMaxRetry: 4,
    levelLoadingMaxRetry: 4,
    fragLoadingMaxRetry: 6,
  });
  player.hls = hls;
  hls.on(Hls.Events.MEDIA_ATTACHED, () => hls.loadSource(source.url));
  hls.on(Hls.Events.MANIFEST_PARSED, () => playVideo());
  hls.on(Hls.Events.ERROR, (_event, data) => onPlayerError(data, generation));
  hls.attachMedia(video);
  scheduleRefresh(source, generation);
  if (!video.currentTime) setStreamView("loading", `Livestream wird geladen${source.title ? `: ${source.title}` : ""}`);
}

// Normales Video (kein Livestream): läuft in Schleife, damit der Bildschirm nie stehen bleibt.
function playFile(source, generation) {
  attachCommon(source);
  const video = elements.video;
  video.loop = true;
  video.src = source.url;
  playVideo();
  scheduleRefresh(source, generation);
  setStreamView("loading", `Video wird geladen${source.title ? `: ${source.title}` : ""}`);
}

function playVideo() {
  const video = elements.video;
  video.play().catch(() => {
    // Mit Ton darf der Browser (etwa am Handy) nicht von selbst starten: dann stumm.
    if (!video.muted) { video.muted = true; video.play().catch(() => {}); }
  });
}

function scheduleRefresh(source, generation) {
  clearTimeout(player.refreshTimer);
  // YouTube-Adressen laufen nach einigen Stunden ab; rechtzeitig eine frische holen.
  if (source.expires_in) player.refreshTimer = setTimeout(() => loadSource(player.url, true, generation), source.expires_in * 1000);
}

function onPlayerError(data, generation) {
  if (!data.fatal || generation !== player.generation || !player.source) return;
  if (player.hls && data.type === window.Hls.ErrorTypes.MEDIA_ERROR && player.retries < 2) {
    player.retries += 1;
    player.hls.recoverMediaError();
    return;
  }
  player.retries += 1;
  freezeFrame();
  elements.playerBox.classList.add("is-frozen");
  player.reconnecting = true;
  const name = streamInfo(player.url).title.split(" · ")[0];
  if (player.retries > PLAYER_MAX_RETRIES) {
    player.reconnecting = false;
    if (fallbackCandidate()) { startFallback(`${name} antwortet nicht`); return; }
    // Der eigene Player kommt nicht durch: lieber eingebettet zeigen als gar nichts.
    toast("Eigener Player gestört · Stream läuft eingebettet");
    playEmbed(player.url);
    return;
  }
  const delay = Math.min(30000, 2000 * 2 ** (player.retries - 1));
  showReconnect(`${name} antwortet nicht`, `Letztes Bild bleibt stehen · Versuch ${player.retries} von ${PLAYER_MAX_RETRIES}`);
  setStreamView("reconnecting", "Verbindung zur Kamera unterbrochen, neuer Versuch läuft");
  clearTimeout(player.retryTimer);
  player.retryTimer = setTimeout(() => loadSource(player.url, true, generation), delay);
}

/* Ersatz bei Ausfall: nächster Favorit, der noch nicht versucht wurde. */
function fallbackCandidate() {
  if (!runtime?.stream?.auto_fallback) return null;
  const primary = runtime.stream.url;
  return (runtime.stream.favorites || []).find((item) => item.url !== primary && item.url !== player.url && !player.tried.has(item.url)) || null;
}

function startFallback(reason) {
  const next = fallbackCandidate();
  if (!next) { playEmbed(player.url); return; }
  const failed = streamInfo(player.override ? player.override.url : runtime.stream.url).title.split(" · ")[0];
  player.tried.add(player.url);
  player.override = next;
  hideReconnect();
  configureStream(runtime.stream, true);
  byId("fbTitle").textContent = `${failed} ist offline`;
  byId("fbSub").textContent = `Zeige jetzt ${next.title || "einen Favoriten"}. Zurück, sobald die Kamera wieder läuft.`;
  byId("fallbackNote").hidden = false;
  console.info("Ersatz-Stream:", reason);
  clearInterval(player.checkTimer);
  player.checkTimer = setInterval(checkPrimary, PRIMARY_CHECK_MS);
  showChip(20000);
}

async function checkPrimary() {
  if (!player.override) { clearInterval(player.checkTimer); return; }
  try {
    const source = await api("/api/stream/source?refresh=1");
    if (source.kind === "hls" || source.kind === "file") {
      endFallback();
      toast("Kamera wieder da · zurück zum ersten Favoriten");
    }
  } catch (_error) { /* nächster Versuch in fünf Minuten */ }
}

function endFallback() {
  clearInterval(player.checkTimer);
  player.override = null;
  player.tried.clear();
  byId("fallbackNote").hidden = true;
  configureStream(runtime.stream, true);
}

function configureStream(stream, force = false) {
  if (gamingPaused()) {
    if (player.signature !== "gaming") {
      stopVideo();
      elements.live.removeAttribute("src");
      player.signature = "gaming";
      player.generation += 1;
    }
    setStreamView("gaming", "Kamera pausiert, solange Gaming läuft");
    return;
  }
  const primary = String(stream.url || "").trim();
  if (player.primary !== null && player.primary !== primary && player.override) {
    // Im Menü wurde eine andere Kamera gewählt: der Ersatz endet.
    clearInterval(player.checkTimer);
    player.override = null;
    player.tried.clear();
    byId("fallbackNote").hidden = true;
  }
  player.primary = primary;
  const url = player.override?.url || primary;
  const signature = JSON.stringify({ url, quality: stream.quality || "auto", fps: stream.max_fps || 30 });
  applyAudio();
  if (!force && signature === player.signature) return;
  const switching = player.signature && JSON.parse(player.signature).url !== url;
  player.signature = signature;
  player.generation += 1;
  player.retries = 0;
  player.reconnecting = false;
  if (!url) {
    stopVideo();
    elements.live.removeAttribute("src");
    player.url = "";
    setStreamView("empty", "Kein Livestream eingerichtet");
    return;
  }
  if (switching) crossfade();
  setStreamView("loading", "Livestream wird vorbereitet");
  loadSource(url, force, player.generation);
}

// Wächter: pausiert der Browser das Video oder bleibt das Bild stehen, holt der Player es zurück.
function watchPlayer() {
  const video = elements.video;
  if (!player.source || !videoActive()) return;
  if (video.paused) playVideo();
  if (video.currentTime !== player.lastTime) {
    player.lastTime = video.currentTime;
    player.lastProgress = Date.now();
    return;
  }
  const stalled = Date.now() - player.lastProgress;
  if (stalled > 8000 && !player.retryTimer) {
    player.reconnecting = true;
    showReconnect("Bild hängt", "Verbindung wird geprüft · letztes Bild bleibt stehen");
  }
  if (stalled > 20000) {
    player.lastProgress = Date.now();
    freezeFrame();
    loadSource(player.url, true, player.generation);
  }
}

function streamHealth() {
  if (!player.url) return { state: "warn", text: "Keine Kamera eingerichtet", last: "–", limit: "–" };
  if (player.reconnecting) return { state: "warn", text: "Letztes Bild, neuer Versuch", last: `Versuch ${player.retries} von ${PLAYER_MAX_RETRIES}`, limit: "8 s ohne neues Bild" };
  if (player.override) return { state: "warn", text: "Ersatz läuft", last: streamInfo(player.url).title, limit: "8 s ohne neues Bild" };
  if (player.kind === "embed") return { state: "warn", text: "Eingebettet (YouTube-Fenster)", last: streamInfo(player.url).title, limit: "–" };
  const video = elements.video;
  const buffered = video.buffered.length ? Math.max(0, video.buffered.end(video.buffered.length - 1) - video.currentTime) : 0;
  const size = video.videoHeight ? `${video.videoHeight}p` : "lädt";
  return { state: "ok", text: `${size} · Puffer ${Math.round(buffered)} s`, last: streamInfo(player.url).channel || streamInfo(player.url).title, limit: "8 s ohne neues Bild" };
}

/* Ton der Kamera: aus, an oder leise und aus, solange Musik läuft. */
function applyAudio() {
  const mode = runtime?.stream?.audio || "off";
  const music = lastState?.music;
  const musicPlaying = !!(music?.enabled && music.playing && !music.paused);
  const video = elements.video;
  video.muted = mode === "off" || (mode === "duck" && musicPlaying);
  video.volume = mode === "duck" ? 0.35 : 1;
}

/* ---------- Stream-Chip und Kamera-Karte ---------- */
let chipTimer = null;
function showChip(duration = 8000) {
  if (!player.url) return;
  const chip = byId("streamChip");
  const info = streamInfo(player.url);
  byId("chipTitle").textContent = info.title;
  const kind = player.kind === "file" ? "Video in Schleife" : player.kind === "embed" ? "eingebettet" : "live";
  byId("chipSub").textContent = [info.channel, kind].filter(Boolean).join(" · ");
  const pill = byId("chipPill");
  pill.textContent = player.kind === "file" ? "VIDEO" : "LIVE";
  pill.classList.toggle("is-off", player.kind === "file" || player.reconnecting);
  chip.classList.remove("is-gone");
  clearTimeout(chipTimer);
  chipTimer = setTimeout(() => { if (byId("camCard").hidden) chip.classList.add("is-gone"); }, duration);
}

function renderCamCard() {
  const url = player.url;
  const info = streamInfo(url);
  byId("camTitle").textContent = info.title;
  const facts = byId("camFacts");
  facts.replaceChildren();
  const add = (key, value) => { if (value) facts.append(el("dt", "", key), el("dd", "", value)); };
  add("Kanal", info.channel);
  add("Wiedergabe", player.kind === "file" ? "Video in Schleife, eigener Player" : player.kind === "embed" ? "eingebettet" : "Livestream, eigener Player");
  add("Bild", elements.video.videoHeight ? `${elements.video.videoHeight}p` : "");
  if (player.override) add("Hinweis", "Ersatz, die eigentliche Kamera ist offline");
}

/* ---------- Läuft gerade (Songkarte) ---------- */
const SOURCE_NAME = { radio: "Radio", ytmusic: "YouTube Music", spotify: "Spotify" };
const SOURCE_ICON = { radio: "i-radio", ytmusic: "t-music", spotify: "i-headphones" };
let nowPlaying = null;
let lastSub = "";
let cardTimer = null;

function artFor(item, className = "", { badge = false, playing = false, paused = false } = {}) {
  const art = el("span", `art${className ? ` ${className}` : ""}${item.kind === "artist" ? " is-round" : ""}`);
  const name = item.title || item.name || "";
  art.style.setProperty("--h", String(hue(name)));
  if (item.cover) {
    const image = el("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = item.cover;
    image.addEventListener("error", () => image.remove());
    art.append(image);
  } else if (item.source === "radio" || item.kind === "station" || item.kind === "artist") {
    art.append(document.createTextNode(initials(name)));
  } else {
    art.append(icon(item.kind === "song" || item.kind === "track" || item.kind === "video" ? "t-music" : "i-list"));
  }
  if (badge && SOURCE_ICON[item.source]) {
    const mark = el("span", "src-badge");
    mark.append(icon(SOURCE_ICON[item.source]));
    art.append(mark);
  }
  if (playing) {
    const eq = el("span", `eq${paused ? " is-paused" : ""}`);
    eq.setAttribute("aria-hidden", "true");
    eq.append(el("i"), el("i"), el("i"), el("i"));
    art.append(eq);
  }
  return art;
}

function musicTarget(music = nowPlaying) {
  if (!music?.playing) return "";
  return music.controls === "pi" || music.controls === "spotify" ? music.controls : "";
}

function nowItem(music) {
  if (!music?.playing) return null;
  if (music.source === "radio") return { source: "radio", kind: "station", id: music.station?.uuid || "", title: music.title, cover: "" };
  return { source: music.source, kind: "song", id: "", title: music.title, cover: music.cover || "" };
}

function isCurrent(entry) {
  const music = nowPlaying;
  if (!music?.playing) return false;
  if (entry.source === "radio") return music.source === "radio" && music.station?.uuid === entry.id;
  return music.source === entry.source && ((!!entry.id && music.track_id === entry.id) || (!!entry.title && music.title === entry.title));
}

function formatDuration(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function musicSub(music) {
  if (music.source === "radio") {
    if (music.loading) return "Verbinde …";
    if (music.paused) return "Pausiert";
    return music.artist ? `Jetzt: ${music.artist}` : music.station?.country || "Radio · live";
  }
  return [music.artist, SOURCE_NAME[music.source], music.loading ? "lädt …" : music.paused ? "pausiert" : ""].filter(Boolean).join(" · ");
}

function renderMusic() {
  const music = lastState?.music;
  nowPlaying = music;
  const card = byId("nowPlaying");
  const show = !!(music?.enabled && music.playing && music.title);
  const wasHidden = card.hidden;
  card.hidden = !show;
  applyAudio();
  renderPlayerTab();
  if (!show) { card.classList.remove("is-open"); return; }
  const radio = music.source === "radio";
  card.classList.toggle("is-paused", !!music.paused);
  card.classList.toggle("is-radio", radio);
  swapArt("npArt", nowItem(music), "np-art");
  byId("npTitle").textContent = music.title;
  const sub = musicSub(music);
  if (sub !== lastSub) {
    const node = byId("npSub");
    node.textContent = sub;
    node.classList.remove("np-sub-anim");
    if (lastSub && !reduceMotion) { void node.offsetWidth; node.classList.add("np-sub-anim"); }
    lastSub = sub;
  }
  byId("npProg").hidden = radio || !music.duration_ms;
  byId("npControls").hidden = !musicTarget(music);
  byId("npVolBtn").hidden = !musicTarget(music);
  if (!musicTarget(music)) card.classList.remove("is-vol");
  $$('[data-mctl="toggle"]').forEach((button) => {
    button.classList.toggle("is-paused", !!music.paused);
    button.setAttribute("aria-label", music.paused ? "Abspielen" : "Pause");
  });
  $$('[data-mctl="previous"]').forEach((button) => button.setAttribute("aria-label", radio ? "Vorheriger Lieblingssender" : "Vorheriger Titel"));
  $$('[data-mctl="next"]').forEach((button) => button.setAttribute("aria-label", radio ? "Nächster Lieblingssender" : "Nächster Titel"));
  updateMusicProgress();
  if (wasHidden && booted && !reduceMotion) card.animate([{ transform: "scale(.94)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 420, easing: "cubic-bezier(.34,1.56,.64,1)" });
}

// Fortschritt und Ausschalt-Timer laufen zwischen den Abfragen lokal weiter.
function updateMusicProgress() {
  const music = nowPlaying;
  if (!music?.playing) return;
  const sleep = byId("npSleep");
  const left = music.sleep_end ? music.sleep_end - Date.now() : 0;
  sleep.hidden = left <= 0;
  if (left > 0) sleep.replaceChildren(icon("i-moon"), document.createTextNode(`aus in ${Math.ceil(left / 60000)} min`));
  if (music.source === "radio" || !music.duration_ms) return;
  const elapsed = music.paused ? 0 : Date.now() - Number(music.fetched_at || Date.now());
  const position = Math.min(music.duration_ms, Number(music.progress_ms || 0) + elapsed);
  const percent = `${((position / music.duration_ms) * 100).toFixed(2)}%`;
  byId("npBar").style.width = percent;
  byId("npLine").style.width = percent;
  byId("npTime").textContent = `${formatDuration(position)} / ${formatDuration(music.duration_ms)}`;
  updatePlayerTime(position, music.duration_ms);
}

// Die Songkarte ändert ihre Größe weich (Breite und Höhe, kein Stauchen); der Inhalt blendet danach ein.
function resizeCard(change) {
  const card = byId("nowPlaying");
  const before = card.getBoundingClientRect();
  const parts = $$(":scope > :not(.np-line)", card);
  const wasShown = new Set(parts.filter((part) => part.getClientRects().length));
  change(card);
  if (reduceMotion || card.hidden || !before.width) return;
  const after = card.getBoundingClientRect();
  if (Math.abs(after.width - before.width) < 1 && Math.abs(after.height - before.height) < 1) return;
  card.getAnimations().forEach((animation) => { if (animation.id === "card-size") animation.cancel(); });
  const grow = card.animate([{ width: `${before.width}px`, height: `${before.height}px` }, { width: `${after.width}px`, height: `${after.height}px` }], { duration: 360, easing: "cubic-bezier(.2,.9,.25,1)" });
  grow.id = "card-size";
  // Nur was neu dazukommt blendet ein; Titel und Bild bleiben stehen.
  parts.filter((part) => !wasShown.has(part) && part.getClientRects().length)
    .forEach((part) => part.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }], { duration: 260, delay: 90, easing: "ease-out", fill: "backwards" }));
}

function setCardOpen(open) {
  const card = byId("nowPlaying");
  if (card.classList.contains("is-open") !== open || card.classList.contains("is-vol")) {
    resizeCard((node) => { node.classList.toggle("is-open", open); node.classList.remove("is-vol"); });
  }
  byId("npOpen").setAttribute("aria-expanded", String(open));
  byId("npVolBtn").setAttribute("aria-expanded", "false");
  clearTimeout(cardTimer);
  if (open) cardTimer = setTimeout(() => setCardOpen(false), 20000);
}

async function sendControl(action, value) {
  const target = musicTarget();
  if (!target) return;
  const before = nowPlaying;
  showMusic(predictMusic(before, action, value));
  try {
    adoptMusic(await post("/api/player/control", { target, action, value }));
  } catch (error) {
    if (before) showMusic(before);
    toast(error.message);
  }
  setTimeout(refreshState, target === "spotify" ? 900 : 1500);
}

/* Musik ohne Verzögerung: jeder Tipp zeigt sofort das erwartete Ergebnis, der Server bestätigt es nur noch. */
let musicHoldUntil = 0;
function showMusic(music, holdMs = 2500) {
  if (!music || !lastState) return;
  musicHoldUntil = Date.now() + holdMs;
  lastState.music = music;
  renderMusic();
}

function musicPosition(music) {
  const elapsed = music.paused ? 0 : Date.now() - Number(music.fetched_at || Date.now());
  return Math.min(Number(music.duration_ms || 0) || Infinity, Number(music.progress_ms || 0) + elapsed);
}

function predictMusic(music, action, value) {
  if (!music?.playing) return null;
  const now = Date.now();
  if (action === "toggle") return { ...music, paused: !music.paused, progress_ms: musicPosition(music), fetched_at: now };
  if (action === "seek") return { ...music, progress_ms: Number(value) * 1000, fetched_at: now };
  if (action === "shuffle") return { ...music, shuffle: !!value };
  if (action === "repeat") return { ...music, repeat: value };
  if (action === "volume") return { ...music, volume: Number(value) };
  if (action === "stop") return { ...music, playing: false };
  if ((action === "next" || action === "previous") && music.queue?.length) {
    // „Zurück“ springt nach drei Sekunden erst an den Anfang des Songs, wie auf dem Handy.
    if (action === "previous" && musicPosition(music) > 3000) return { ...music, progress_ms: 0, fetched_at: now };
    const index = music.queue_index + (action === "next" ? 1 : -1);
    const track = music.queue.find((item) => item.position === index);
    if (!track) return null;
    return { ...music, title: track.title, artist: track.artist, cover: track.cover || music.cover, track_id: track.id, liked: track.liked, duration_ms: (track.duration || 0) * 1000,
      progress_ms: 0, queue_index: index, paused: false, loading: true, fetched_at: now };
  }
  return null;
}

function predictPlay(entry, { title, subtitle, cover, duration = 0 } = {}) {
  const local = entry.source !== "spotify";
  return { enabled: true, ok: true, playing: true, controls: local ? "pi" : "spotify", device: local ? "pi" : nowPlaying?.device || "", source: entry.source,
    title: entry.source === "radio" ? entry.title : title || entry.title, artist: entry.source === "radio" ? "" : subtitle || entry.subtitle || "", cover: cover || entry.cover || "",
    station: entry.station || null, loading: true, paused: false, progress_ms: 0, duration_ms: duration * 1000, fetched_at: Date.now(),
    volume: nowPlaying?.volume ?? null, queue: [], queue_index: 0, shuffle: false, repeat: nowPlaying?.repeat || "off" };
}

// Der Pi antwortet mit seinem neuen Zustand: sofort zeigen, statt auf die nächste Abfrage zu warten.
function adoptMusic(result) {
  if (!result?.music || !lastState) return;
  showMusic({ ...result.music, fetched_at: result.music.fetched_at || Date.now() }, 800);
}

/* ---------- Zustand vom Server ---------- */
let lastRefresh = 0;
// Läuft Musik und das Menü ist offen, kommt der Zustand öfter, damit Titel und Zeit nicht hinterherhängen.
function refreshDue() {
  const fast = isPlayingNow() && !byId("sheet").hidden;
  return Date.now() - lastRefresh >= (fast ? 2000 : 5000);
}
function applyRuntime(next) {
  if (!next?.ui) return;
  const firstRun = !runtime;
  runtime = next;
  computeLayout();
  applyTheme();
  applyBlocks();
  configureStream(runtime.stream || {});
  applyNight();
  if (firstRun) armCompact();
}

async function refreshState() {
  try {
    const state = await api("/api/state");
    // Gerade getippte Musik-Befehle nicht von einer älteren Antwort überschreiben lassen.
    if (Date.now() < musicHoldUntil && lastState?.music) state.music = lastState.music;
    lastState = state;
    lastRefresh = Date.now();
    if (backendDown) {
      backendDown = false;
      setStreamView(player.url ? "playing" : "empty", "Backend wieder erreichbar");
      if (player.url) configureStream(runtime?.stream || {}, true);
    }
    applyRuntime({ ...state.config, ui: state.config.ui });
    renderWeather();
    const go = renderBoards();
    if (go && booted) expandFor(60000);
    renderWarnings();
    renderMusic();
    renderStatus();
    if (!byId("sheet").hidden) refreshSheetLive();
    document.body.classList.remove("is-loading");
  } catch (_error) {
    backendDown = true;
    if (!videoActive() && !elements.live.getAttribute("src")) setStreamView("down", "Lokales Backend nicht erreichbar");
    renderStatus();
  }
}

/* ---------- Menü (Seitenpanel) ---------- */
let menuTab = "home";
async function loadConfig() {
  const result = await api("/api/config");
  meta.youtubeKeySet = !!result.youtube_key_set;
  meta.spotifyConnected = !!result.spotify_connected;
  meta.ytConnected = !!result.ytmusic_connected;
  currentConfig = result.config;
  return currentConfig;
}

async function adminStatus() {
  const status = await api("/api/admin/status");
  meta.remote = !!status.remote;
  meta.pinLogin = !!status.pin_login;
  return status;
}

async function beginAdmin(tab = "home") {
  menuTab = tab;
  try {
    const status = await adminStatus();
    if (status.authenticated) {
      await loadConfig();
      openSheet(tab);
      return;
    }
    openAuth(status);
  } catch (error) {
    toast(`Menü nicht verfügbar: ${error.message}`);
  }
}

function flipPlayer(apply) {
  const box = elements.playerBox;
  const before = box.getBoundingClientRect();
  apply();
  if (window.innerWidth > 900 || reduceMotion) return;
  const after = box.getBoundingClientRect();
  if (!after.width || (before.width === after.width && before.left === after.left)) return;
  box.animate([
    { transformOrigin: "0 0", transform: `translate(${before.left - after.left}px, ${before.top - after.top}px) scale(${before.width / after.width}, ${before.height / after.height})` },
    { transformOrigin: "0 0", transform: "none" },
  ], { duration: 460, easing: "cubic-bezier(.2,.9,.25,1)" });
}

function openSheet(page = "home") {
  closeDetails(true);
  const sheet = byId("sheet");
  sheet.getAnimations().forEach((animation) => animation.cancel());
  sheet.style.transform = "";
  resetPages();
  flipPlayer(() => { sheet.hidden = false; document.body.classList.add("sheet-open"); });
  syncSheet();
  enterPage();
  if (page !== "home") goTo(page, { animate: false });
  applyNight();
}

function closeSheet(dragged = false) {
  const sheet = byId("sheet");
  if (sheet.hidden) return;
  hideKeyboard();
  stopPanelPolling();
  stopViz();
  const done = () => {
    flipPlayer(() => { sheet.hidden = true; document.body.classList.remove("sheet-open"); });
    armCompact();
    applyNight();
  };
  closeAction();
  if (reduceMotion || dragged === true) { done(); return; }
  sheet.animate([{ transform: "none", opacity: 1 }, { transform: "translateX(-40%)", opacity: 0 }], { duration: 260, easing: "cubic-bezier(.4,0,.9,.6)" }).finished.then(done).catch(done);
}

function pill(box, selector, className = "seg-pill") {
  if (!box) return;
  let marker = $(`:scope > .${className}`, box);
  const fresh = !marker;
  if (fresh) {
    marker = el("span", className);
    marker.setAttribute("aria-hidden", "true");
    box.prepend(marker);
  }
  const target = $(selector, box);
  if (!target || !target.offsetWidth) { marker.style.opacity = "0"; return; }
  const jump = fresh || marker.style.opacity === "0";
  if (jump) marker.style.transition = "none";
  marker.style.opacity = "1";
  marker.style.width = `${target.offsetWidth}px`;
  marker.style.height = `${target.offsetHeight}px`;
  marker.style.transform = `translate(${target.offsetLeft}px, ${target.offsetTop}px)`;
  if (jump) { void marker.offsetWidth; marker.style.transition = ""; }
}

function refreshPills() {
  if (byId("sheet").hidden) return;
  pill(byId("distSeg"), '[aria-pressed="true"]');
  pill(byId("srcSeg"), '[aria-pressed="true"]');
}

// Neu geladene Listen rutschen sanft nacheinander herein; nur Lage und Deckkraft, kein erzwungenes Layout.
function stagger(box) {
  if (reduceMotion || !box) return;
  [...box.children].slice(0, 14).forEach((child, index) => {
    child.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }], { duration: 360, delay: index * 26, easing: "cubic-bezier(.2,.9,.25,1)", fill: "backwards" });
  });
}

let panelTimers = [];
function stopPanelPolling() {
  panelTimers.forEach(clearTimeout);
  panelTimers = [];
}

function syncSheet() {
  const config = currentConfig;
  if (!config) return;
  byId("qualSel").value = config.stream.quality === "1080" ? "auto" : config.stream.quality || "auto";
  byId("fpsSel").value = String(config.stream.max_fps || 30);
  byId("camAudio").value = config.stream.audio || "off";
  byId("autoFav").checked = !!config.stream.auto_fallback;
  byId("cfgStream").value = config.stream.url || "";
  byId("placeNow").textContent = config.location.label;
  byId("placeSub").textContent = `${Math.abs(config.location.latitude).toFixed(2).replace(".", ",")}° ${config.location.latitude >= 0 ? "N" : "S"} · ${Math.abs(config.location.longitude).toFixed(2).replace(".", ",")}° ${config.location.longitude >= 0 ? "O" : "W"} · ${config.app.timezone}`;
  byId("skySel").value = config.ui.sky || "auto";
  $$("#distSeg button").forEach((button) => button.setAttribute("aria-pressed", String(Number(button.dataset.dist) === Number(config.ui.distance || 2))));
  $$("[data-block]").forEach((input) => { input.checked = !!config.ui[input.dataset.block]?.enabled; });
  byId("compactSel").value = String(config.ui.compact_after ?? 120);
  byId("warnSel").value = config.weather.warnings || "moderate";
  byId("nightOn").checked = !!config.ui.night?.enabled;
  byId("nightFrom").value = config.ui.night?.start || "23:00";
  byId("nightTo").value = config.ui.night?.end || "06:00";
  byId("nightOff").checked = !!config.ui.night?.screen_off;
  byId("leaveMode").checked = config.transit.leave_mode !== false;
  byId("cfgMusicSource").value = config.music.source || "off";
  byId("cfgSpotifyClient").value = config.music.spotify?.client_id || "";
  byId("cfgYoutubeQuery").value = config.youtube?.default_query ?? "";
  byId("youtubeKeyState").textContent = meta.youtubeKeySet
    ? "Ein Schlüssel ist hinterlegt; die Suche nutzt die offizielle YouTube-API. Zum Ersetzen neuen Schlüssel eintragen."
    : "Kein Schlüssel nötig: Die Suche liest die öffentliche YouTube-Suche mit dem Filter „Live“.";
  byId("publicUrl").value = config.server?.public_url || "";
  byId("lanOn").checked = !!config.server?.lan_access;
  applyTheme();
  refreshPills();
  syncPickers();
  if (!byId("sheet").hidden) {
    if (isPage("live")) refreshSheetLive();
    if (isPage("transit")) renderStopCards();
    if (isPage("home")) renderHome();
  }
}

/* Livestream-Bereich */
let liveSearched = false;
function stateLabel() {
  if (!player.url) return ["Keine Kamera", "is-warn"];
  if (player.reconnecting) return ["Neuer Versuch", "is-warn"];
  if (player.override) return ["Ersatz", "is-warn"];
  if (player.kind === "embed") return ["Eingebettet", "is-warn"];
  if (player.kind === "file") return ["Video", ""];
  return ["Spielt", ""];
}

function thumbNode(url, className = "thumb") {
  const box = el("span", className);
  const source = thumbnailFor(url);
  if (source) {
    const image = el("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = source;
    box.append(image);
  }
  return box;
}

function refreshSheetLive() {
  if (!currentConfig) return;
  const url = player.url || currentConfig.stream.url;
  const info = streamInfo(url || "");
  byId("nowTitle").textContent = url ? info.title : "Keine Kamera eingerichtet";
  byId("nowSub").textContent = url ? [info.channel, player.kind === "file" ? "Video in Schleife" : player.kind === "embed" ? "eingebettet" : "live"].filter(Boolean).join(" · ") : "Unten suchen oder eine Adresse eintragen";
  const image = byId("nowThumbImg");
  const source = thumbnailFor(url);
  image.hidden = !source;
  if (source && image.getAttribute("src") !== source) image.src = source;
  const [label, stateClass] = stateLabel();
  byId("nowState").replaceChildren(el("span", `dot ${stateClass}`.trim()), el("span", "", label));
  renderFavorites();
  $$("#liveResults .result").forEach((card) => card.classList.toggle("is-current", card.dataset.url === currentConfig.stream.url));
}

let favoritesSig = "";
function renderFavorites() {
  const favorites = currentConfig.stream.favorites || [];
  const list = byId("favList");
  const signature = JSON.stringify([favorites, player.url, player.override, currentConfig.stream.url]);
  if (signature === favoritesSig && list.childElementCount) return;
  favoritesSig = signature;
  if (!favorites.length) {
    list.replaceChildren(el("p", "hint", "Noch keine Favoriten. In der Suche unten den Stern antippen."));
    return;
  }
  list.replaceChildren(...favorites.map((favorite, index) => {
    const row = el("div", "row");
    const text = el("div");
    const playing = favorite.url === player.url;
    text.append(el("strong", "", `${index + 1}. ${favorite.title || streamInfo(favorite.url).title}`), el("span", "sub", playing ? (player.override ? "springt gerade ein" : "spielt gerade") : favorite.channel || "Favorit"));
    const actions = el("div", "acts");
    const up = el("button", "icon-button small");
    up.type = "button";
    up.append(icon("i-up"));
    up.setAttribute("aria-label", "Nach oben");
    up.disabled = index === 0;
    up.addEventListener("click", () => saveConfig((config) => {
      const items = config.stream.favorites;
      items.splice(index - 1, 0, items.splice(index, 1)[0]);
    }, "Reihenfolge der Favoriten"));
    const play = el("button", "button button-secondary", favorite.url === currentConfig.stream.url ? "Läuft" : "Abspielen");
    play.type = "button";
    play.disabled = favorite.url === currentConfig.stream.url;
    play.addEventListener("click", () => chooseStream(favorite));
    const remove = el("button", "icon-button small");
    remove.type = "button";
    remove.append(icon("i-trash"));
    remove.setAttribute("aria-label", "Favorit entfernen");
    remove.addEventListener("click", () => saveConfig((config) => { config.stream.favorites.splice(index, 1); }, "Favorit entfernt"));
    actions.append(up, play, remove);
    row.append(thumbNode(favorite.url), text, actions);
    return row;
  }));
}

async function chooseStream(video) {
  const url = video.url || video.embed_url;
  titles.set(url, { title: video.title, channel: video.channel });
  player.override = null;
  player.tried.clear();
  byId("fallbackNote").hidden = true;
  await saveConfig((config) => { config.stream.url = url; }, `Spielt jetzt · ${video.title || "neue Kamera"}`);
  showChip(10000);
}

function toggleFavorite(video, star) {
  const url = video.url || video.embed_url;
  const known = (currentConfig.stream.favorites || []).some((item) => item.url === url);
  saveConfig((config) => {
    if (known) config.stream.favorites = config.stream.favorites.filter((item) => item.url !== url);
    else config.stream.favorites.push({ url, title: video.title || "", channel: video.channel || "" });
  }, known ? "Favorit entfernt" : "Als Favorit gemerkt").then((saved) => {
    if (!saved) return;
    star.classList.toggle("is-on", !known);
    if (!known) { star.classList.remove("pop"); void star.offsetWidth; star.classList.add("pop"); }
  });
}

function liveCard(video, onPick, { star = true } = {}) {
  const url = video.embed_url;
  const card = el("div", `result${url === currentConfig?.stream?.url ? " is-current" : ""}`);
  card.dataset.url = url;
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  if (video.thumbnail) {
    const image = el("img");
    image.alt = "";
    image.loading = "lazy";
    image.src = video.thumbnail;
    card.append(image);
  } else {
    const fallback = el("span", "thumb-fallback");
    fallback.append(icon("t-live"));
    card.append(fallback);
  }
  card.append(el("span", "live-pill", "LIVE"));
  if (star) {
    const on = (currentConfig?.stream?.favorites || []).some((item) => item.url === url);
    const button = el("button", `star${on ? " is-on" : ""}`);
    button.type = "button";
    button.append(icon("i-star"));
    button.setAttribute("aria-label", on ? "Aus Favoriten entfernen" : "Als Favorit merken");
    button.addEventListener("click", (event) => { event.stopPropagation(); toggleFavorite(video, button); });
    card.append(button);
  }
  card.append(el("strong", "", video.title), el("span", "", video.channel));
  card.addEventListener("click", () => onPick(video, card));
  card.addEventListener("keydown", (event) => { if (event.key === "Enter") onPick(video, card); });
  return card;
}

function skeletons(box, count = 3) {
  box.replaceChildren(...Array.from({ length: count }, () => el("div", "skel")));
}

function busyButton(button, on) {
  if (!button) return;
  button.disabled = on;
  if (on) {
    button.dataset.label = button.dataset.label || button.textContent;
    const dots = el("span", "dots");
    dots.append(el("i"), el("i"), el("i"));
    button.replaceChildren(dots);
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
}

async function searchLive(query) {
  liveSearched = true;
  byId("liveQuery").value = query;
  const box = byId("liveResults");
  const button = byId("liveSearchBtn");
  busyButton(button, true);
  skeletons(box, 4);
  setStatus(byId("liveStatus"), "");
  try {
    const result = await api(`/api/live/search?q=${encodeURIComponent(query)}`);
    if (!result.results.length) {
      box.replaceChildren();
      setStatus(byId("liveStatus"), "Gerade ist dazu nichts live. Anderes Suchwort versuchen.");
      return;
    }
    result.results.forEach((video) => titles.set(video.embed_url, { title: video.title, channel: video.channel }));
    box.replaceChildren(...result.results.map((video) => liveCard({ ...video, url: video.embed_url }, (picked) => chooseStream(picked))));
    stagger(box);
  } catch (error) {
    box.replaceChildren();
    setStatus(byId("liveStatus"), error.message, "error");
  } finally {
    busyButton(button, false);
  }
}

/* Ort */
async function searchPlaces(input, box, status, onPick, button) {
  const query = input.value.trim();
  hideKeyboard();
  if (query.length < 2) { setStatus(status, "Bitte mindestens zwei Zeichen eingeben.", "error"); return; }
  busyButton(button, true);
  skeletons(box, 3);
  setStatus(status, "");
  try {
    const result = await api(`/api/location/search?q=${encodeURIComponent(query)}`);
    if (!result.results.length) { box.replaceChildren(); setStatus(status, "Kein Ort gefunden. Andere Schreibweise versuchen."); return; }
    box.replaceChildren(...result.results.map((place) => {
      const pick = el("button", "pick");
      pick.type = "button";
      const text = el("div");
      text.append(el("strong", "", place.name), el("span", "", [place.region, place.timezone].filter(Boolean).join(" · ")));
      pick.append(text, icon("i-chev"));
      pick.addEventListener("click", () => onPick(place, pick));
      return pick;
    }));
    stagger(box);
  } catch (error) {
    box.replaceChildren();
    setStatus(status, error.message, "error");
  } finally {
    busyButton(button, false);
  }
}

async function timezoneFor(place) {
  if (place.timezone) return place.timezone;
  try {
    return (await api(`/api/location/timezone?lat=${place.latitude}&lon=${place.longitude}`)).timezone;
  } catch (_error) {
    return "";
  }
}

async function choosePlace(place) {
  const zone = await timezoneFor(place);
  const saved = await saveConfig((config) => {
    config.location = { label: place.name, latitude: Number(place.latitude), longitude: Number(place.longitude) };
    if (zone) config.app.timezone = zone;
  }, `Ort ${place.name}`);
  if (saved) byId("placeResults").replaceChildren();
}

/* Anzeige */
const THEMES = [
  { id: "auto", name: "Automatisch" }, { id: "dark", name: "Dunkel" }, { id: "light", name: "Hell" },
  { id: "high-contrast", name: "Kontrast" }, { id: "y2k", name: "Y2K" }, { id: "dagobert", name: "Dagobert" },
  { id: "flap", name: "Fallblatt" }, { id: "paper", name: "Papier" }, { id: "plasma", name: "Plasma" },
];
function buildThemeGrid() {
  byId("themeGrid").replaceChildren(...THEMES.map((theme) => {
    const card = el("button", "theme-card");
    card.type = "button";
    card.dataset.theme = theme.id;
    const mini = el("span", "mini");
    (theme.id === "auto" ? ["light", "dark"] : [theme.id]).forEach((preview) => {
      const bar = el("span", "mini-bar");
      bar.dataset.preview = preview;
      bar.append(el("span", "", "21:19"), el("b", "", "31"));
      mini.append(bar);
    });
    card.append(mini, el("strong", "", theme.name));
    card.addEventListener("click", () => {
      if (currentConfig?.ui.theme === theme.id) return;
      saveConfig((config) => { config.ui.theme = theme.id; }, `Theme ${theme.name}`);
    });
    return card;
  }));
}

/* ÖPNV */
function boardFor(stop) {
  return (lastState?.boards || []).find((board) => board.station_id && board.station_id === stop.station_id);
}

function renderStopCards() {
  if (!currentConfig) return;
  const stops = currentConfig.transit.stops || [];
  const box = byId("stopCards");
  if (!stops.length) { box.replaceChildren(el("p", "hint", "Noch keine Haltestelle. Unten suchen und hinzufügen.")); return; }
  box.replaceChildren(...stops.map((stop, index) => {
    const board = boardFor(stop);
    const card = el("div", "card");
    const head = el("div", "stop-head");
    const title = el("div");
    title.append(el("strong", "", stop.title || board?.title || stop.station_name), el("div", "hint", `${stop.station_name} · VRR`));
    const state = el("span", "state-pill");
    const bad = board && !board.ok;
    state.append(el("span", `dot${bad ? " is-bad" : board ? "" : " is-warn"}`), document.createTextNode(bad ? "keine Antwort" : board ? "Echtzeit" : "wird geladen"));
    const actions = el("div", "stop-actions");
    const up = el("button", "icon-button small");
    up.type = "button";
    up.append(icon("i-up"));
    up.setAttribute("aria-label", "Nach oben");
    up.disabled = index === 0;
    up.addEventListener("click", () => saveConfig((config) => { const items = config.transit.stops; items.splice(index - 1, 0, items.splice(index, 1)[0]); }, "Reihenfolge der Haltestellen"));
    const remove = el("button", "icon-button small");
    remove.type = "button";
    remove.append(icon("i-trash"));
    remove.setAttribute("aria-label", "Haltestelle entfernen");
    remove.addEventListener("click", () => saveConfig((config) => { config.transit.stops.splice(index, 1); }, `${stop.station_name} entfernt`));
    actions.append(up, remove);
    head.append(title, state, actions);
    const walk = el("div", "stop-walk");
    walk.append(el("span", "hint", "Gehzeit zur Haltestelle"));
    const stepper = el("div", "stepper");
    const minus = el("button", "icon-button small", "−");
    minus.type = "button";
    minus.setAttribute("aria-label", "Eine Minute weniger");
    const output = el("output", "", `${stop.walk_minutes || 0} min`);
    const plus = el("button", "icon-button small", "+");
    plus.type = "button";
    plus.setAttribute("aria-label", "Eine Minute mehr");
    const step = (delta) => {
      const value = Math.max(0, Math.min(30, (stop.walk_minutes || 0) + delta));
      if (value !== (stop.walk_minutes || 0)) saveConfig((config) => { config.transit.stops[index].walk_minutes = value; }, `Gehzeit ${stop.station_name} ${value} min`);
    };
    minus.addEventListener("click", () => step(-1));
    plus.addEventListener("click", () => step(1));
    stepper.append(minus, output, plus);
    walk.append(stepper);
    const chips = el("div", "chips");
    const filter = stop.line_filter || [];
    const lines = (board?.lines || []).map((item) => item.line);
    filter.forEach((line) => { if (!lines.includes(line)) lines.push(line); });
    const all = el("button", "chip", "Alle Linien");
    all.type = "button";
    all.setAttribute("aria-pressed", String(!filter.length));
    all.addEventListener("click", () => { if (filter.length) saveConfig((config) => { config.transit.stops[index].line_filter = []; }, "Alle Linien"); });
    chips.append(all);
    lines.forEach((line) => {
      const chip = el("button", "chip", line);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(filter.includes(line)));
      chip.addEventListener("click", () => saveConfig((config) => {
        const current = config.transit.stops[index].line_filter || [];
        config.transit.stops[index].line_filter = current.includes(line) ? current.filter((item) => item !== line) : [...current, line];
      }, `Linienfilter ${stop.station_name}`));
      chips.append(chip);
    });
    card.append(head, walk, chips);
    if (!lines.length) card.append(el("span", "hint", "Linien erscheinen hier, sobald die ersten Abfahrten geladen sind."));
    return card;
  }));
}

async function searchStops(input, box, status, onPick, button) {
  const query = input.value.trim();
  hideKeyboard();
  if (query.length < 2) { setStatus(status, "Bitte mindestens zwei Zeichen eingeben.", "error"); return; }
  busyButton(button, true);
  skeletons(box, 3);
  setStatus(status, "");
  try {
    const result = await api(`/api/station/search?q=${encodeURIComponent(query)}`);
    if (!result.results.length) { box.replaceChildren(); setStatus(status, "Keine VRR-Haltestelle gefunden. Andere Schreibweise versuchen."); return; }
    box.replaceChildren(...result.results.map((station) => {
      const pick = el("button", "pick");
      pick.type = "button";
      const text = el("div");
      text.append(el("strong", "", station.station_name), el("span", "", "VRR"));
      pick.append(text, el("span", "", "hinzufügen"));
      pick.addEventListener("click", () => onPick(station, pick));
      return pick;
    }));
    stagger(box);
  } catch (error) {
    box.replaceChildren();
    setStatus(status, error.message, "error");
  } finally {
    busyButton(button, false);
  }
}

function stopFrom(station) {
  return { title: "", station_name: station.station_name, station_id: station.station_id, line_filter: [], nightbus_only: false, hide_if_empty: true, max_rows: Number(currentConfig?.transit?.default_max_rows || 2), walk_minutes: 0 };
}

async function addStop(station) {
  if ((currentConfig.transit.stops || []).some((stop) => stop.station_id === station.station_id)) { toast("Diese Haltestelle ist schon da"); return; }
  if (await saveConfig((config) => { config.transit.stops.push(stopFrom(station)); config.ui.transit.enabled = true; }, `${station.station_name} hinzugefügt`)) {
    byId("stopResults").replaceChildren();
  }
}

/* ---------- Musik ---------- */
const music = { src: "radio", filter: "", results: [], playlists: [], recent: [], searched: false };
const RADIO_FILTERS = [["", "Alle"], ["de", "Deutschland"], ["news", "Nachrichten"], ["pop", "Pop"], ["rock", "Rock"], ["jazz", "Jazz"], ["klassik", "Klassik"]];
const LIBRARY_FILTERS = [["", "Alle"], ["song", "Songs"], ["artist", "Künstler"], ["album", "Alben"], ["playlist", "Playlists"]];
const KIND_LABELS = { song: "Song", video: "Video", album: "Album", playlist: "Playlist", artist: "Künstler", track: "Song", station: "Sender" };

function radioItem(station) {
  return { source: "radio", kind: "station", id: station.uuid, title: station.name, subtitle: [station.country, station.codec && station.bitrate ? `${station.codec} · ${station.bitrate} kbit/s` : station.codec].filter(Boolean).join(" · "), station };
}

async function openMusicPanel() {
  renderMusicPanel();
  loadRecent();
  if (!music.searched) searchMusic(true);
  loadBluetooth(false);
}

/* ---------- Player-Reiter: großes Bild, Spulen, Zufall, Wiederholen, Klangregler, Warteschlange ---------- */
const EQ_LABELS = ["60", "150", "400", "1k", "2,4k", "15k"];
const EQ_PRESETS = {
  flat: ["Neutral", [0, 0, 0, 0, 0, 0]], bass: ["Mehr Bass", [6, 4, 1, 0, 0, 0]], pop: ["Pop", [-1, 2, 4, 3, 1, -1]],
  rock: ["Rock", [4, 2, -2, -1, 2, 4]], klassik: ["Klassik", [3, 1, 0, 0, 1, 3]], sprache: ["Sprache", [-4, -2, 2, 4, 3, 0]],
  akustik: ["Akustik", [2, 1, 1, 2, 2, 2]], nacht: ["Nachts leise", [-3, -1, 0, 1, 0, -4]],
};
const REPEAT_NEXT = { off: "all", all: "one", one: "off" };
const REPEAT_LABEL = { off: "Wiederholen aus", all: "Alle wiederholen", one: "Einen Titel wiederholen" };
const pp = { seeking: false, level: 0, target: 0, frame: 0, levelTimer: null, queueSig: "", spotifyQueue: [], spotifyTrack: "", eqTimer: null, eqBuilt: false, drag: null };

function playerVisible() {
  return isPage("player");
}

function splitSong(text) {
  const [artist, title] = String(text || "").split(" – ");
  return title ? { artist, title } : { artist: "", title: artist };
}

function renderPlayerTab() {
  const current = nowPlaying;
  const playing = isPlayingNow();
  renderMiniPlayer(current, playing);
  renderHome();
  syncVolume(current);
  $$("#mFavs .tile, #mRecent .rcard, #mResults .mrow, .page-body .track-row").forEach((node) => node.classList.toggle("is-playing", !!node.dataset.key && isCurrent(JSON.parse(node.dataset.key))));
  byId("ppEmpty").hidden = playing;
  byId("pp").hidden = !playing;
  if (!playing) return;
  byId("pp").classList.toggle("is-paused", !!current.paused);
  const radio = current.source === "radio";
  const song = radio && current.artist && !current.loading ? splitSong(current.artist) : null;
  const before = byId("ppArt");
  const art = swapArt("ppArt", { ...nowItem(current), cover: current.cover || "" }, "pp-art");
  if (art !== before && booted && !reduceMotion) art.animate([{ opacity: 0, transform: "scale(.94)" }, { opacity: 1, transform: "none" }], { duration: 420, easing: "cubic-bezier(.2,.9,.25,1.05)" });
  byId("pp").classList.toggle("is-loading", !!current.loading);
  byId("ppSrc").replaceChildren(icon(SOURCE_ICON[current.source] || "t-music"), document.createTextNode(radio ? `Radio · ${current.title}` : `${SOURCE_NAME[current.source] || ""}${current.device && current.device !== "pi" ? ` · ${current.device}` : ""}`));
  byId("ppTitle").textContent = song?.title || current.title;
  byId("ppSub").textContent = radio ? (song ? song.artist || "live" : musicSub(current)) : current.artist || "";
  const likeable = !radio && !!current.track_id;
  byId("ppLike").hidden = !likeable;
  byId("ppAdd").hidden = !likeable;
  byId("ppLike").setAttribute("aria-pressed", String(!!current.liked));
  byId("ppLike").classList.toggle("is-on", !!current.liked);
  byId("ppTime").hidden = radio || !current.duration_ms;
  byId("ppShuffle").hidden = radio;
  byId("ppRepeat").hidden = radio;
  byId("ppShuffle").setAttribute("aria-pressed", String(!!current.shuffle));
  const repeat = current.repeat || "off";
  byId("ppRepeat").dataset.mode = repeat;
  byId("ppRepeat").setAttribute("aria-label", REPEAT_LABEL[repeat]);
  byId("eqHint").textContent = current.controls === "spotify" ? "Spotify auf dem Pi lässt sich nicht regeln" : "wirkt bei Radio und YouTube Music auf dem Pi";
  if (playerVisible()) renderQueue(current);
}

// Lautstärke und Ausschalt-Timer gibt es auf der Startseite und im Player; beide zeigen denselben Stand.
const VOLUME_SLIDERS = ["mVol", "homeVol", "npVol"];

function setVolumeSliders(value) {
  VOLUME_SLIDERS.forEach((id) => {
    byId(id).value = String(value);
    byId(id).style.setProperty("--p", `${value}%`);
    byId(`${id}Out`).textContent = String(value);
  });
}

// Wer an der Lautstärke dreht, dem klappt die Karte nicht unter dem Finger zu.
function keepCardOpen() {
  const card = byId("nowPlaying");
  if (!card.classList.contains("is-open") && !card.classList.contains("is-vol")) return;
  clearTimeout(cardTimer);
  cardTimer = setTimeout(() => setCardOpen(false), card.classList.contains("is-open") ? 20000 : 8000);
}
const SLEEP_GROUPS = ["sleepChips", "homeSleep"];
function syncVolume(current) {
  if (!current) return;
  VOLUME_SLIDERS.forEach((id) => {
    const slider = byId(id);
    if (document.activeElement !== slider && current.volume !== null && current.volume !== undefined) {
      slider.value = String(current.volume);
      byId(`${id}Out`).textContent = String(current.volume);
    }
    slider.style.setProperty("--p", `${slider.value}%`);
  });
  const sleepEnd = current.sleep_end || 0;
  SLEEP_GROUPS.forEach((id) => $$(`#${id} .chip`).forEach((chip) => chip.setAttribute("aria-pressed", String(!!sleepEnd && chip.dataset.sleep === (current.sleep_label || "")))));
}

// Mini-Player unten im Menü auf allen Seiten außer Startseite und Player.
// Der Mini-Player schwebt über den Seiten: Ein- und Ausblenden verschiebt keinen Inhalt.
function renderMiniPlayer(current, playing) {
  const mini = byId("miniPlayer");
  const show = playing && !byId("sheet").hidden && !["home", "player"].includes(currentPage());
  const shown = !mini.hidden && !mini.classList.contains("is-leaving");
  byId("sheet").classList.toggle("has-mini", show);
  if (!show) {
    if (shown && !reduceMotion) {
      mini.classList.add("is-leaving");
      mini.animate([{ transform: "none", opacity: 1 }, { transform: "translateY(60%)", opacity: 0 }], { duration: 200, easing: "cubic-bezier(.4,0,1,1)", fill: "forwards" })
        .finished.then((animation) => { if (mini.classList.contains("is-leaving")) mini.hidden = true; mini.classList.remove("is-leaving"); animation.cancel(); }).catch(() => {});
    } else if (!mini.classList.contains("is-leaving")) {
      mini.hidden = true;
    }
    return;
  }
  if (!shown) {
    mini.getAnimations().forEach((animation) => animation.cancel());
    mini.classList.remove("is-leaving");
    mini.hidden = false;
    if (booted && !reduceMotion) mini.animate([{ transform: "translateY(60%)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 380, delay: 120, easing: "cubic-bezier(.34,1.3,.64,1)", fill: "backwards" });
  }
  swapArt("miniArt", { ...nowItem(current), cover: current.cover || "" });
  mini.classList.toggle("is-paused", !!current.paused);
  mini.classList.toggle("is-loading", !!current.loading);
  const { title, sub } = songParts(current);
  byId("miniTitle").textContent = title;
  byId("miniSub").textContent = sub;
}

function updatePlayerTime(position, duration) {
  if (pp.seeking) return;
  const seek = byId("ppSeek");
  seek.value = String(Math.round((position / duration) * 1000));
  seek.style.setProperty("--p", `${(position / duration) * 100}%`);
  byId("ppPos").textContent = formatDuration(position);
  byId("ppDur").textContent = `-${formatDuration(duration - position)}`;
}

/* Warteschlange: YouTube Music zum Springen, Entfernen und Verschieben; Spotify nur ansehen; Radio zeigt die letzten Titel. */
function queueRow(item, { current = false, editable = false } = {}) {
  const row = el("div", `mrow track-row${current ? " is-playing" : ""}`);
  row.dataset.position = String(item.position ?? "");
  const text = el("span", "txt");
  text.append(el("strong", "", item.title || "Wird geladen …"), el("span", "sub", [item.artist, item.duration ? formatDuration(item.duration * 1000) : ""].filter(Boolean).join(" · ")));
  row.append(artFor({ title: item.title, cover: item.cover, kind: "song" }, "", { playing: current, paused: nowPlaying?.paused }), text);
  const actions = el("span", "acts");
  if (editable && !current) {
    const remove = el("button", "icon-button small flat");
    remove.type = "button";
    remove.append(icon("i-close"));
    remove.setAttribute("aria-label", "Aus der Warteschlange entfernen");
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      row.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(40px)" }], { duration: 220, easing: "ease-in" }).finished.then(() => row.remove()).catch(() => {});
      sendControl("remove", item.position);
    });
    const grip = el("span", "grip");
    grip.append(icon("i-grip"));
    grip.setAttribute("aria-label", "Ziehen zum Verschieben");
    grip.addEventListener("pointerdown", (event) => startQueueDrag(event, row));
    actions.append(remove, grip);
  }
  row.append(actions);
  if (editable && !current) row.addEventListener("click", () => sendControl("jump", item.position));
  return row;
}

function renderQueue(current) {
  if (pp.drag) return;
  const list = byId("queueList");
  const head = $("span", byId("queueHead"));
  const hint = byId("queueHint");
  let rows = [];
  let signature = "";
  if (current.source === "radio") {
    head.textContent = "Zuletzt im Radio";
    hint.textContent = "Titel aus dem Stream des Senders";
    const titles = current.recent_titles || [];
    signature = JSON.stringify(titles);
    rows = titles.map((item, index) => queueRow({ ...splitSong(item.title), cover: item.cover, duration: 0 }, { current: index === 0 && !current.paused }));
    if (!rows.length) rows = [el("p", "hint", "Dieser Sender schickt keine Titel mit, oder der erste läuft gerade an.")];
  } else if (current.source === "spotify") {
    head.textContent = "Als Nächstes";
    hint.textContent = "bei Spotify nur zum Ansehen";
    if (current.track_id !== pp.spotifyTrack) {
      pp.spotifyTrack = current.track_id;
      api("/api/player/queue").then((result) => { pp.spotifyQueue = result.results || []; pp.queueSig = ""; renderQueue(nowPlaying); }).catch(() => { pp.spotifyQueue = []; });
    }
    signature = JSON.stringify(pp.spotifyQueue);
    rows = pp.spotifyQueue.map((item) => queueRow(item));
    if (!rows.length) rows = [el("p", "hint", "Spotify verrät gerade nicht, was als Nächstes kommt.")];
  } else {
    head.textContent = "Warteschlange";
    hint.textContent = current.shuffle ? "gemischt · antippen springt, ziehen verschiebt" : "antippen springt, ziehen verschiebt";
    const queue = current.queue || [];
    signature = JSON.stringify([queue.map((item) => item.id), current.queue_index]);
    rows = queue.map((item) => queueRow(item, { current: item.position === current.queue_index, editable: true }));
  }
  if (signature === pp.queueSig) return;
  pp.queueSig = signature;
  list.replaceChildren(...rows);
}

function startQueueDrag(event, row) {
  event.preventDefault();
  event.stopPropagation();
  const rows = $$("#queueList .track-row");
  const startIndex = rows.indexOf(row);
  const height = row.offsetHeight + 8;
  pp.drag = { row, startY: event.clientY, startIndex, target: startIndex, rows, height };
  row.classList.add("is-dragging");
  row.setPointerCapture(event.pointerId);
  const move = (moveEvent) => {
    const delta = moveEvent.clientY - pp.drag.startY;
    row.style.transform = `translateY(${delta}px) scale(1.02)`;
    const target = Math.max(0, Math.min(rows.length - 1, startIndex + Math.round(delta / height)));
    pp.drag.target = target;
    rows.forEach((other, index) => {
      if (other === row) return;
      const shift = startIndex < target && index > startIndex && index <= target ? -height : startIndex > target && index < startIndex && index >= target ? height : 0;
      other.style.transform = shift ? `translateY(${shift}px)` : "";
    });
  };
  const end = () => {
    row.removeEventListener("pointermove", move);
    row.removeEventListener("pointerup", end);
    row.removeEventListener("pointercancel", end);
    const { target } = pp.drag;
    rows.forEach((other) => { other.style.transform = ""; });
    row.classList.remove("is-dragging");
    pp.drag = null;
    if (target !== startIndex) {
      const from = Number(row.dataset.position);
      const to = Number(rows[target].dataset.position);
      const anchor = target > startIndex ? rows[target].nextSibling : rows[target];
      byId("queueList").insertBefore(row, anchor);
      pp.queueSig = "";
      sendControl("move", { from, to });
    }
  };
  row.addEventListener("pointermove", move);
  row.addEventListener("pointerup", end);
  row.addEventListener("pointercancel", end);
}

/* Klangregler */
function currentGains() {
  return currentConfig?.music?.equalizer?.gains || nowPlaying?.equalizer || [0, 0, 0, 0, 0, 0];
}

function buildEqualizer() {
  if (pp.eqBuilt) { syncEqualizer(); return; }
  pp.eqBuilt = true;
  byId("eqPresets").replaceChildren(...Object.entries(EQ_PRESETS).map(([key, [label, gains]]) => {
    const chip = el("button", "chip", label);
    chip.type = "button";
    chip.dataset.preset = key;
    chip.addEventListener("click", () => { setBands(gains, true); saveEqualizer(key, gains, `Klang ${label}`); });
    return chip;
  }));
  byId("eqBands").replaceChildren(...EQ_LABELS.map((label, index) => {
    const band = el("label", "eq-band");
    const value = el("output", "", "0");
    const input = el("input");
    input.type = "range";
    input.min = "-12";
    input.max = "12";
    input.step = "1";
    input.value = "0";
    input.setAttribute("aria-label", `${label} Hz`);
    input.addEventListener("input", () => {
      value.textContent = formatGain(Number(input.value));
      const gains = $$("#eqBands input").map((node) => Number(node.value));
      markPreset("custom");
      clearTimeout(pp.eqTimer);
      pp.eqTimer = setTimeout(() => saveEqualizer("custom", gains, ""), 450);
    });
    band.append(value, input, el("span", "", label));
    return band;
  }));
  syncEqualizer();
}

function formatGain(gain) {
  return gain > 0 ? `+${gain}` : String(gain);
}

function setBands(gains, animate = false) {
  $$("#eqBands input").forEach((input, index) => {
    const to = Number(gains[index] || 0);
    if (!animate || reduceMotion) { input.value = String(to); input.previousElementSibling.textContent = formatGain(to); return; }
    // Regler gleiten zum neuen Wert, statt zu springen.
    const from = Number(input.value);
    const startedAt = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - startedAt) / 380);
      const eased = 1 - (1 - progress) ** 3;
      input.value = String(Math.round(from + (to - from) * eased));
      input.previousElementSibling.textContent = formatGain(Number(input.value));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

function markPreset(key) {
  $$("#eqPresets .chip").forEach((chip) => chip.setAttribute("aria-pressed", String(chip.dataset.preset === key)));
}

function syncEqualizer() {
  if (document.activeElement?.closest?.("#eqBands")) return;
  setBands(currentGains());
  markPreset(currentConfig?.music?.equalizer?.preset || "flat");
}

function saveEqualizer(preset, gains, label) {
  markPreset(preset);
  saveConfig((config) => { config.music.equalizer = { preset, gains: gains.map(Number) }; }, label || "Klangregler", { quiet: !label, undo: !!label });
}

/* Bewegter Equalizer über dem Bild: folgt dem echten Pegel des Pi-Players, sonst einer ruhigen Bewegung. */
function startViz() {
  if (pp.frame || reduceMotion) return;
  // 30 Bilder pro Sekunde reichen für die Balken und lassen dem Video auf dem Pi genug Luft.
  let last = 0;
  const loop = (time) => { if (time - last >= 32) { last = time; drawViz(time); } pp.frame = requestAnimationFrame(loop); };
  pp.frame = requestAnimationFrame(loop);
  pp.levelTimer = setInterval(pollLevel, 140);
}

function stopViz() {
  cancelAnimationFrame(pp.frame);
  pp.frame = 0;
  clearInterval(pp.levelTimer);
  pp.levelTimer = null;
}

async function pollLevel() {
  const current = nowPlaying;
  if (!current?.playing || current.paused) { pp.target = 0; return; }
  if (current.controls !== "pi") { pp.target = 0.6; return; }
  try { pp.target = (await api("/api/player/level")).level || 0; } catch (_error) { pp.target = 0.5; }
}

function drawViz(time) {
  const canvas = byId("ppViz");
  if (!canvas || !canvas.offsetWidth) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.offsetWidth * ratio;
  const height = canvas.offsetHeight * ratio;
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  pp.level += (pp.target - pp.level) * 0.22;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, width, height);
  const bars = 28;
  const gap = width / bars;
  const gains = currentGains();
  context.fillStyle = "rgba(255, 255, 255, 0.82)";
  for (let index = 0; index < bars; index += 1) {
    const band = Math.min(gains.length - 1, Math.floor((index / bars) * gains.length));
    const weight = 0.75 + Number(gains[band] || 0) / 30;
    const wave = 0.5 + 0.5 * Math.sin(time / (170 + (index % 5) * 35) + index * 1.7) * Math.sin(time / 610 + index);
    const value = Math.max(0.04, Math.min(1, pp.level * weight * (0.45 + 0.75 * wave)));
    const barHeight = value * height * 0.92;
    context.beginPath();
    context.roundRect(index * gap + gap * 0.18, height - barHeight, gap * 0.64, barHeight, Math.min(6 * ratio, gap * 0.3));
    context.fill();
  }
}

/* Zu Playlist hinzufügen */
async function toggleAddList() {
  const list = byId("ppAddList");
  const open = list.hidden;
  byId("ppAdd").setAttribute("aria-expanded", String(open));
  if (!open) { list.hidden = true; return; }
  list.hidden = false;
  skeletons(list, 2);
  const current = nowPlaying;
  try {
    const result = await post("/api/player/playlists", { service: current.source });
    const own = (result.results || []).filter((item) => !["LM", "VLLM"].includes(item.id));
    list.replaceChildren(...own.map((item) => {
      const pick = el("button", "pick");
      pick.type = "button";
      const text = el("div");
      text.append(el("strong", "", item.title), el("span", "", item.subtitle || "Playlist"));
      pick.append(artFor({ ...item, source: current.source }), text, icon("i-plus"));
      pick.addEventListener("click", async () => {
        try {
          await post("/api/player/add", { service: current.source, playlist: item.id, id: current.track_id });
          toast(`Zu „${item.title}“ hinzugefügt`);
          list.hidden = true;
          byId("ppAdd").setAttribute("aria-expanded", "false");
        } catch (error) { toast(error.message); }
      });
      return pick;
    }));
    if (!own.length) list.replaceChildren(el("p", "hint", "Keine eigenen Playlists gefunden."));
    stagger(list);
  } catch (error) {
    list.replaceChildren(el("p", "hint", error.message));
  }
}

async function toggleLike() {
  const current = nowPlaying;
  if (!current?.track_id) return;
  const liked = !current.liked;
  const button = byId("ppLike");
  button.classList.toggle("is-on", liked);
  button.setAttribute("aria-pressed", String(liked));
  if (liked && !reduceMotion) { button.classList.remove("pop"); void button.offsetWidth; button.classList.add("pop"); }
  try {
    await post("/api/player/like", { service: current.source, id: current.track_id, liked });
    nowPlaying = { ...current, liked };
    toast(liked ? "Zu „Gefällt mir“ hinzugefügt" : "Aus „Gefällt mir“ entfernt");
  } catch (error) {
    button.classList.toggle("is-on", !liked);
    toast(error.message);
  }
}

/* ---------- Musik: Seiten für Album, Playlist und Künstler ---------- */
const PAGE_KINDS = new Set(["album", "playlist", "artist"]);

function openEntry(entry) {
  if (entry.source !== "radio" && PAGE_KINDS.has(entry.kind)) openLibraryPage(entry);
  else playEntry(entry);
}

async function openLibraryPage(entry) {
  const body = pushDynamicPage(KIND_LABELS[entry.kind] || "");
  skeletons(body, 4);
  try {
    const result = await post("/api/player/open", { service: entry.source, kind: entry.kind, id: entry.id });
    renderPage(body, { ...result.results, source: entry.source }, entry);
  } catch (error) {
    body.replaceChildren(el("p", "hint", error.message));
  }
}

function totalMinutes(tracks) {
  const seconds = tracks.reduce((sum, track) => sum + Number(track.duration || 0), 0);
  return seconds ? `${Math.round(seconds / 60)} min` : "";
}

function renderPage(body, page, entry) {
  const hero = el("div", "page-hero");
  const art = artFor({ ...entry, cover: page.cover || entry.cover }, `page-art${entry.kind === "artist" ? " is-round" : ""}`);
  const text = el("div", "page-text");
  text.append(el("strong", "", page.title || entry.title), el("span", "", page.subtitle || entry.subtitle || ""), el("span", "hint", [`${page.tracks.length} Titel`, totalMinutes(page.tracks)].filter(Boolean).join(" · ")));
  const buttons = el("div", "button-row");
  const playAll = el("button", "button button-primary");
  playAll.type = "button";
  playAll.append(icon("i-play"), document.createTextNode("Abspielen"));
  playAll.addEventListener("click", () => playFrom(entry, page, 0, false));
  const shuffle = el("button", "button button-secondary");
  shuffle.type = "button";
  shuffle.append(icon("i-shuffle"), document.createTextNode("Zufall"));
  shuffle.addEventListener("click", () => playFrom(entry, page, 0, true));
  buttons.append(playAll, shuffle);
  playAll.disabled = shuffle.disabled = !page.tracks.length;
  text.append(buttons);
  hero.append(art, text);
  const nodes = [hero];
  if (entry.kind === "artist" && page.tracks.length) nodes.push(el("h2", "sec", "Beliebte Titel"));
  const list = el("div", "list tracks");
  list.append(...page.tracks.map((track, index) => {
    const key = { source: entry.source, id: track.id, title: track.title };
    const row = el("div", `mrow track-row${isCurrent(key) ? " is-playing" : ""}`);
    row.dataset.key = JSON.stringify(key);
    row.setAttribute("role", "button");
    row.tabIndex = 0;
    const number = el("span", "track-no", String(index + 1));
    const textBox = el("span", "txt");
    textBox.append(el("strong", "", track.title), el("span", "sub", [track.artist, track.duration ? formatDuration(track.duration * 1000) : ""].filter(Boolean).join(" · ")));
    row.append(entry.kind === "album" ? number : artFor({ ...track, kind: "song" }), textBox, el("span"));
    row.addEventListener("click", () => playFrom(entry, page, index, false, track));
    return row;
  }));
  if (!page.tracks.length) list.append(el("p", "hint", "Hier gibt es keine abspielbaren Titel."));
  nodes.push(list);
  if (page.albums?.length) {
    nodes.push(el("h2", "sec", "Alben und Singles"));
    const tiles = el("div", "tiles");
    tiles.append(...page.albums.map((album) => tile({ ...album, source: entry.source })));
    nodes.push(tiles);
  }
  body.replaceChildren(...nodes);
  stagger(body);
}

async function playFrom(entry, page, start, shuffle, track = null) {
  const artist = entry.kind === "artist";
  const kind = artist && entry.source === "ytmusic" ? "playlist" : entry.kind;
  const id = artist ? page.play_id || entry.id : entry.id;
  const before = nowPlaying;
  const first = track || page.tracks[shuffle ? 0 : start] || {};
  showMusic(predictPlay({ ...entry, kind: "song" }, { title: shuffle && !track ? page.title : first.title, subtitle: first.artist || page.subtitle, cover: first.cover || page.cover, duration: first.duration || 0 }), 8000);
  try {
    if (artist && entry.source === "spotify" && track) {
      adoptMusic(await post("/api/player/play", { service: entry.source, kind: "song", id: track.id, title: track.title, subtitle: track.artist, cover: track.cover }));
    } else {
      adoptMusic(await post("/api/player/play", { service: entry.source, kind, id, start, shuffle, title: page.title || entry.title, subtitle: page.subtitle || "", cover: page.cover || entry.cover }));
    }
    setTimeout(loadRecent, 800);
  } catch (error) {
    musicHoldUntil = 0;
    if (before) showMusic(before, 0);
    toast(error.message);
  }
  setTimeout(refreshState, 1500);
}

/* ---------- Menü-Navigation: Startseite mit Kacheln, Seiten schieben sich wie am iPhone darüber ---------- */
// Seiten ohne Eintrag hängen direkt an der Startseite.
const PARENT_PAGE = { music: "player", accounts: "music" };
const PAGE_SPRING = "cubic-bezier(.2,.9,.25,1)";
const nav = { stack: [] };

const pageOf = (name) => $(`#stack > .page[data-panel="${name}"]`);
const topPage = () => nav.stack.at(-1);
const currentPage = () => topPage()?.dataset.panel || "home";
const isPage = (name) => !byId("sheet").hidden && currentPage() === name;

function resetPages() {
  $$("#stack > .page").forEach((page) => {
    page.getAnimations().forEach((animation) => animation.cancel());
    page.style.transform = "";
    page.style.opacity = "";
    if (page.classList.contains("is-dynamic")) page.remove();
    else page.hidden = !page.classList.contains("is-home");
  });
  nav.stack = [pageOf("home")];
}

// Was beim Betreten einer Seite geladen oder gestartet wird.
const PAGE_ENTER = {
  home: () => renderHome(),
  live: () => { refreshSheetLive(); if (!liveSearched) searchLive(currentConfig?.youtube?.default_query || "Live Cam"); },
  display: () => pill(byId("distSeg"), '[aria-pressed="true"]'),
  transit: () => renderStopCards(),
  player: () => { pp.queueSig = ""; renderPlayerTab(); buildEqualizer(); startViz(); },
  music: () => openMusicPanel(),
  accounts: () => { renderAccounts(); loadBluetooth(true); loadSpotifyPair(); pollYtLogin(true); },
  system: () => { renderDiag(); loadSystemInfo(); },
  phone: () => startPairing(),
  gaming: () => loadGaming(),
};

function enterPage() {
  stopPanelPolling();
  hideKeyboard();
  const name = currentPage();
  if (name !== "player") stopViz();
  renderMiniPlayer(nowPlaying, isPlayingNow());
  PAGE_ENTER[name]?.();
}

// Eine Seite bewegen: Endlage halten (fill), danach aufräumen; so blitzt nie die alte Lage auf.
function slidePage(page, frames, duration, done = null) {
  page.getAnimations().forEach((animation) => animation.cancel());
  page.classList.add("is-moving");
  const animation = page.animate(frames, { duration, easing: PAGE_SPRING, fill: "forwards" });
  const settle = () => {
    if (done) done();
    page.classList.remove("is-moving");
    animation.cancel();
  };
  animation.finished.then(settle).catch(() => page.classList.remove("is-moving"));
}

function pushPage(page, { animate = true } = {}) {
  const from = topPage();
  if (!page || from === page) return;
  page.getAnimations().forEach((animation) => animation.cancel());
  page.style.transform = "";
  page.style.opacity = "";
  page.hidden = false;
  const scroller = $(":scope > .scroll", page);
  if (scroller) scroller.scrollTop = 0;
  nav.stack.push(page);
  enterPage();
  if (from && animate && !reduceMotion) {
    slidePage(from, [{ transform: "none", opacity: 1 }, { transform: "translateX(-28%)", opacity: 0.4 }], 420, () => { if (topPage() !== from) from.hidden = true; });
    slidePage(page, [{ transform: "translateX(100%)" }, { transform: "none" }], 420);
  } else if (from) {
    from.getAnimations().forEach((animation) => animation.cancel());
    from.hidden = true;
  }
}

function popPage({ animate = true, fromX = 0 } = {}) {
  if (nav.stack.length < 2) return;
  const page = nav.stack.pop();
  const to = topPage();
  to.getAnimations().forEach((animation) => animation.cancel());
  to.style.transform = "";
  to.style.opacity = "";
  to.hidden = false;
  const finish = () => {
    page.style.transform = "";
    if (page.classList.contains("is-dynamic")) page.remove();
    else if (!nav.stack.includes(page)) page.hidden = true;
  };
  enterPage();
  if (animate && !reduceMotion) {
    const share = Math.min(1, fromX / Math.max(1, page.offsetWidth));
    slidePage(to, [{ transform: `translateX(${-28 * (1 - share)}%)`, opacity: 0.4 + 0.6 * share }, { transform: "none", opacity: 1 }], 360);
    slidePage(page, [{ transform: `translateX(${fromX}px)` }, { transform: "translateX(100%)" }], 320, finish);
  } else {
    finish();
  }
}

// Springt zu einer Seite: ist sie schon im Stapel, geht es zurück, sonst werden die Elternseiten mit aufgelegt.
function goTo(name, { animate = true } = {}) {
  const target = pageOf(name);
  if (!target) return;
  const index = nav.stack.indexOf(target);
  if (index >= 0) {
    while (nav.stack.length > index + 1) popPage({ animate: animate && nav.stack.length === index + 2 });
    return;
  }
  const chain = [];
  let step = name;
  while (step !== "home" && !nav.stack.includes(pageOf(step))) {
    chain.unshift(step);
    step = PARENT_PAGE[step] || "home";
  }
  const keep = nav.stack.indexOf(pageOf(step));
  while (nav.stack.length > keep + 1) popPage({ animate: false });
  chain.forEach((page, position) => pushPage(pageOf(page), { animate: animate && position === chain.length - 1 }));
}

function openTile(name) {
  // „Musik“ zeigt den Player; läuft nichts, geht es gleich weiter zum Durchsuchen.
  goTo(name === "player" && !isPlayingNow() ? "music" : name);
}

function pushDynamicPage(title) {
  const page = el("section", "panel page is-dynamic");
  page.dataset.panel = "library";
  page.setAttribute("aria-label", title || "Seite");
  const bar = el("header", "bar");
  const back = el("button", "back");
  back.type = "button";
  back.dataset.back = "";
  back.append(icon("i-back"), el("span", "", "Zurück"));
  const close = el("button", "icon-button flat bar-close");
  close.type = "button";
  close.dataset.closeSheet = "";
  close.setAttribute("aria-label", "Menü schließen");
  close.append(icon("i-close"));
  const end = el("div", "bar-end");
  end.append(close);
  bar.append(back, el("h2", "bar-title", title), end);
  const scroller = el("div", "scroll");
  const body = el("div", "page-body");
  scroller.append(body);
  page.append(bar, scroller);
  byId("stack").append(page);
  pushPage(page);
  return body;
}

/* Startseite: was läuft, Lautstärke und Timer, darunter die Kacheln mit dem aktuellen Stand */
function isPlayingNow() {
  return !!(nowPlaying?.enabled && nowPlaying.playing && nowPlaying.title);
}

function songParts(current) {
  const song = current.source === "radio" && current.artist && !current.loading ? splitSong(current.artist) : null;
  return { title: song?.title || current.title, sub: song ? `${song.artist || "live"} · ${current.title}` : musicSub(current) };
}

function renderHome() {
  if (byId("sheet").hidden) return;
  const current = nowPlaying;
  const playing = isPlayingNow();
  const hero = byId("homeNow");
  const wasHidden = hero.hidden;
  hero.hidden = !playing;
  if (playing) {
    swapArt("homeArt", { ...nowItem(current), cover: current.cover || "" });
    const { title, sub } = songParts(current);
    byId("homeTitle").textContent = title;
    byId("homeSub").textContent = sub;
    byId("homeSrc").replaceChildren(icon(SOURCE_ICON[current.source] || "t-music"), document.createTextNode(current.source === "radio" ? "Radio" : SOURCE_NAME[current.source] || ""));
    hero.classList.toggle("is-paused", !!current.paused);
    hero.classList.toggle("is-loading", !!current.loading);
    if (wasHidden && booted && !reduceMotion) hero.animate([{ opacity: 0, transform: "translateY(10px) scale(.98)" }, { opacity: 1, transform: "none" }], { duration: 420, easing: PAGE_SPRING });
  }
  byId("subPlayer").textContent = playing ? `${current.paused ? "Pausiert" : "Läuft"}: ${songParts(current).title}` : "Radio, YouTube Music, Spotify";
  const config = currentConfig;
  if (!config) return;
  const url = player.url || config.stream.url;
  byId("subLive").textContent = url ? `${streamInfo(url).title} · ${stateLabel()[0]}` : "Keine Kamera eingerichtet";
  const stops = config.transit.stops || [];
  byId("subTransit").textContent = stops.length ? `${stops.length} ${stops.length === 1 ? "Haltestelle" : "Haltestellen"}${config.transit.leave_mode !== false ? " · Losgeh-Zeit" : ""}` : "Keine Haltestelle";
  const weather = lastState?.weather;
  byId("subPlace").textContent = [config.location.label, weather?.ok ? `${weather.temp}° ${weather.condition || ""}`.trim() : ""].filter(Boolean).join(" · ");
  byId("subDisplay").textContent = [THEMES.find((theme) => theme.id === (config.ui.theme || "dark"))?.name, config.ui.night?.enabled ? "Nachtmodus an" : ""].filter(Boolean).join(" · ");
  const health = Object.values(lastState?.health || {});
  const broken = health.filter((source) => source && source.ok === false).length;
  byId("subSystem").textContent = broken ? `${broken} ${broken === 1 ? "Quelle gestört" : "Quellen gestört"}` : "Alles in Ordnung";
  byId("subSystem").classList.toggle("is-bad", broken > 0);
}

// Bilder nur austauschen, wenn sich etwas ändert: ein ersetztes Element verschluckt sonst den Tipp.
function swapArt(id, item, className = "", options = {}) {
  const node = byId(id);
  const key = JSON.stringify([item.cover || "", item.title || item.name || "", item.kind || "", item.source || "", className, options.playing, options.paused]);
  if (node.dataset.key === key) return node;
  const art = artFor(item, className, options);
  art.id = id;
  art.dataset.key = key;
  node.replaceWith(art);
  return art;
}

/* ---------- Gesten: nach links wischen schließt das Menü, vom linken Rand zurück ---------- */
function setupGestures() {
  const sheet = byId("sheet");
  let drag = null;
  let suppressUntil = 0;
  // Nach einem Wisch darf der Knopf unter dem Finger nicht auch noch auslösen.
  sheet.addEventListener("click", (event) => {
    if (performance.now() < suppressUntil) { event.stopPropagation(); event.preventDefault(); }
  }, true);
  const ignore = (target) => target.closest("input, textarea, select, .recent, .eq-bands, #eqPresets, .chips, .grip, .osk, .seg");
  sheet.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button > 0 || ignore(event.target)) { drag = null; return; }
    const left = sheet.getBoundingClientRect().left;
    drag = { x: event.clientX, y: event.clientY, t: performance.now(), mode: "", edge: nav.stack.length > 1 && event.clientX - left < 36, width: sheet.offsetWidth, id: event.pointerId };
  });
  sheet.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.mode) {
      if (Math.hypot(dx, dy) < 12) return;
      if (Math.abs(dx) < Math.abs(dy) * 1.5) { drag = null; return; }
      drag.mode = dx > 0 ? (drag.edge ? "back" : "") : "close";
      if (!drag.mode) { drag = null; return; }
      try { sheet.setPointerCapture(event.pointerId); } catch (_error) { /* nicht schlimm */ }
      if (drag.mode === "back") {
        drag.page = topPage();
        drag.below = nav.stack.at(-2);
        drag.below.hidden = false;
      }
    }
    if (drag.mode === "close") {
      sheet.style.transform = `translateX(${Math.min(0, dx)}px)`;
    } else {
      const x = Math.max(0, dx);
      const share = Math.min(1, x / drag.width);
      drag.page.style.transform = `translateX(${x}px)`;
      drag.below.style.transform = `translateX(${-28 * (1 - share)}%)`;
      drag.below.style.opacity = String(0.4 + 0.6 * share);
    }
  });
  const finish = (event, cancelled = false) => {
    if (!drag || event.pointerId !== drag.id) return;
    const active = drag;
    drag = null;
    if (!active.mode) return;
    suppressUntil = performance.now() + 400;
    const dx = cancelled ? 0 : event.clientX - active.x;
    const velocity = dx / Math.max(1, performance.now() - active.t);
    if (active.mode === "close") {
      if (dx < -active.width * 0.28 || velocity < -0.55) {
        sheet.animate([{ transform: `translateX(${dx}px)` }, { transform: `translateX(-${active.width}px)` }], { duration: 220, easing: "cubic-bezier(.4,0,1,1)" }).finished
          .then(() => { sheet.style.transform = ""; closeSheet(true); }).catch(() => { sheet.style.transform = ""; });
      } else {
        sheet.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: 420, easing: "cubic-bezier(.34,1.4,.64,1)" });
        sheet.style.transform = "";
      }
      return;
    }
    const x = Math.max(0, dx);
    active.below.style.transform = "";
    active.below.style.opacity = "";
    active.page.style.transform = "";
    if (x > active.width * 0.3 || velocity > 0.55) {
      popPage({ fromX: x });
    } else {
      const share = x / active.width;
      active.page.animate([{ transform: `translateX(${x}px)` }, { transform: "none" }], { duration: 360, easing: "cubic-bezier(.34,1.3,.64,1)" });
      active.below.animate([{ transform: `translateX(${-28 * (1 - share)}%)`, opacity: 0.4 + 0.6 * share }, { transform: "translateX(-28%)", opacity: 0.4 }], { duration: 300, easing: PAGE_SPRING })
        .finished.then(() => { if (topPage() === active.page) active.below.hidden = true; }).catch(() => {});
    }
  };
  sheet.addEventListener("pointerup", (event) => finish(event));
  sheet.addEventListener("pointercancel", (event) => finish(event, true));
  // Kacheln, Zurück und Schließen per Delegation, damit sie auch auf nachgeladenen Seiten gehen.
  sheet.addEventListener("click", (event) => {
    const open = event.target.closest("[data-open]");
    if (open) { openTile(open.dataset.open); return; }
    if (event.target.closest("[data-back]")) { popPage(); return; }
    if (event.target.closest("[data-close-sheet]")) closeSheet();
  });
}

function renderMusicPanel() {
  $$("#srcSeg button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.src === music.src)));
  pill(byId("srcSeg"), `[data-src="${music.src}"]`);
  byId("mQueryLabel").textContent = music.src === "radio" ? "Sender, Stadt oder Genre suchen" : music.src === "ytmusic" ? "Song, Album, Playlist oder Künstler" : "In Spotify suchen";
  byId("mQuery").placeholder = music.src === "radio" ? "z. B. WDR 2, Jazz, Köln" : "z. B. Bohemian Rhapsody";
  const filters = music.src === "radio" ? RADIO_FILTERS : LIBRARY_FILTERS;
  byId("mFilters").replaceChildren(...filters.map(([key, label]) => {
    const chip = el("button", "chip", label);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(music.filter === key));
    chip.addEventListener("click", () => {
      music.filter = key;
      if (music.src === "radio") searchMusic(); else { renderMusicPanel(); renderResults(true); }
    });
    return chip;
  }));
  renderFavoriteTiles();
  renderRecent();
  renderResults(false);
  renderPlayerTab();
  renderAccounts();
}

function entryKey(entry) {
  return JSON.stringify({ source: entry.source, id: entry.id, title: entry.source === "radio" ? "" : entry.title });
}

function tile(entry) {
  const button = el("button", `tile${isCurrent(entry) ? " is-playing" : ""}`);
  button.type = "button";
  button.dataset.key = entryKey(entry);
  button.append(artFor(entry, "", { playing: isCurrent(entry), paused: nowPlaying?.paused }), el("strong", "", entry.title));
  if (entry.source !== "radio" && entry.subtitle) button.append(el("span", "sub", entry.subtitle));
  button.addEventListener("click", () => openEntry(entry));
  return button;
}

function renderFavoriteTiles() {
  const head = byId("mFavHead");
  const box = byId("mFavs");
  if (music.src === "radio") {
    head.firstElementChild.textContent = "Lieblingssender";
    const stations = currentConfig?.music?.radio?.favorites || [];
    box.replaceChildren(...stations.map((station) => tile(radioItem(station))));
    if (!stations.length) box.replaceChildren(el("p", "hint", "Noch keine Lieblingssender. Beim Sender den Stern antippen."));
    return;
  }
  head.firstElementChild.textContent = "Meine Playlists";
  const connected = music.src === "spotify" ? meta.spotifyConnected : meta.ytConnected;
  if (!connected) { box.replaceChildren(el("p", "hint", `${SOURCE_NAME[music.src]} ist noch nicht verbunden (unten unter „Konten und Lautsprecher“).`)); return; }
  box.replaceChildren(...music.playlists.map((item) => tile({ ...item, source: music.src })));
  if (!music.playlists.length) box.replaceChildren(el("p", "hint", "Playlists werden geladen …"));
}

function renderRecent() {
  const box = byId("mRecent");
  byId("mRecentHead").hidden = !music.recent.length;
  box.replaceChildren(...music.recent.map((entry) => {
    const card = el("button", `rcard${isCurrent(entry) ? " is-playing" : ""}`);
    card.type = "button";
    card.dataset.key = entryKey(entry);
    card.append(artFor(entry, "", { badge: true, playing: isCurrent(entry), paused: nowPlaying?.paused }), el("strong", "", entry.title), el("span", "sub", entry.source === "radio" ? "Radio" : entry.subtitle || SOURCE_NAME[entry.source]));
    card.addEventListener("click", () => openEntry(entry));
    return card;
  }));
}

async function loadRecent() {
  try {
    music.recent = (await api("/api/music/recent")).results || [];
    renderRecent();
  } catch (_error) { /* Verlauf ist nur ein Komfort */ }
}

function musicRow(entry) {
  const row = el("div", `mrow${isCurrent(entry) ? " is-playing" : ""}`);
  row.setAttribute("role", "button");
  row.tabIndex = 0;
  row.dataset.key = entryKey(entry);
  const text = el("span", "txt");
  text.append(el("strong", "", entry.title), el("span", "sub", entry.source === "radio" ? entry.subtitle : [KIND_LABELS[entry.kind], entry.subtitle].filter(Boolean).join(" · ")));
  row.append(artFor(entry, "", { playing: isCurrent(entry), paused: nowPlaying?.paused }), text);
  if (entry.source === "radio") {
    const on = (currentConfig?.music?.radio?.favorites || []).some((station) => station.uuid === entry.id);
    const star = el("button", `fav${on ? " is-on" : ""}`);
    star.type = "button";
    star.append(icon("i-star"));
    star.setAttribute("aria-label", on ? "Aus Lieblingssendern entfernen" : "Als Lieblingssender merken");
    star.addEventListener("click", (event) => { event.stopPropagation(); toggleStation(entry.station, star); });
    row.append(star);
  } else if (PAGE_KINDS.has(entry.kind)) {
    const more = el("span", "chev");
    more.append(icon("i-back"));
    row.append(more);
  } else {
    row.append(el("span"));
  }
  row.addEventListener("click", () => openEntry(entry));
  row.addEventListener("keydown", (event) => { if (event.key === "Enter") openEntry(entry); });
  return row;
}

function renderResults(animate) {
  const box = byId("mResults");
  const head = byId("mResHead");
  const items = music.src === "radio" ? music.results : music.results.filter((item) => !music.filter || item.kind === music.filter || (music.filter === "song" && item.kind === "video"));
  head.firstElementChild.textContent = music.src === "radio" ? "Sender" : "Ergebnisse";
  $("small", head).textContent = music.searched ? `${items.length} gefunden${music.src === "radio" ? " · Radio Browser" : ""}` : "";
  head.hidden = !music.searched && !items.length;
  const nodes = [];
  if (music.src === "radio") items.forEach((item) => nodes.push(musicRow(item)));
  else ["artist", "song", "video", "album", "playlist"].forEach((kind) => {
    const group = items.filter((item) => item.kind === kind);
    if (!group.length) return;
    nodes.push(el("div", "group-head", { song: "Songs", video: "Videos", artist: "Künstler", album: "Alben", playlist: "Playlists" }[kind]));
    group.forEach((item) => nodes.push(musicRow(item)));
  });
  if (!nodes.length && music.searched) nodes.push(el("p", "hint", "Nichts gefunden. Anderes Suchwort oder den Filter „Alle“ probieren."));
  box.replaceChildren(...nodes);
  if (animate) stagger(box);
}

async function searchMusic(initial = false) {
  const query = byId("mQuery").value.trim();
  const status = byId("mStatus");
  hideKeyboard();
  setStatus(status, "");
  if (music.src !== "radio") {
    const connected = music.src === "spotify" ? meta.spotifyConnected : meta.ytConnected;
    if (!connected) { music.results = []; renderResults(false); setStatus(status, `${SOURCE_NAME[music.src]} ist noch nicht verbunden.`, "error"); return; }
    if (!query) { if (!initial) setStatus(status, "Bitte ein Suchwort eingeben."); return; }
  }
  const button = byId("mSearch");
  busyButton(button, true);
  skeletons(byId("mResults"), 3);
  try {
    if (music.src === "radio") {
      const result = await post("/api/radio/search", { query, filter: music.filter });
      music.results = result.results.map(radioItem);
    } else {
      const result = await post("/api/player/search", { service: music.src, query });
      music.results = result.results.map((item) => ({ ...item, source: music.src }));
    }
    music.searched = true;
    renderMusicPanel();
    renderResults(true);
  } catch (error) {
    music.results = [];
    renderResults(false);
    setStatus(status, error.message, "error");
  } finally {
    busyButton(button, false);
  }
}

async function loadPlaylists() {
  if (music.src === "radio") return;
  const service = music.src;
  try {
    const result = await post("/api/player/playlists", { service });
    if (service === music.src) { music.playlists = result.results || []; renderFavoriteTiles(); }
  } catch (error) {
    if (service === music.src) byId("mFavs").replaceChildren(el("p", "hint", error.message));
  }
}

async function playEntry(entry) {
  const status = byId("mStatus");
  setStatus(status, "");
  const before = nowPlaying;
  showMusic(predictPlay(entry, { duration: entry.duration || 0 }), 6000);
  try {
    if (entry.source === "radio") {
      if (!entry.station?.url) throw new Error("Sender ohne Adresse");
      adoptMusic(await post("/api/radio/play", { station: entry.station }));
    } else {
      adoptMusic(await post("/api/player/play", { service: entry.source, kind: entry.kind, id: entry.id, title: entry.title, subtitle: entry.subtitle, cover: entry.cover }));
    }
    setTimeout(loadRecent, 800);
  } catch (error) {
    musicHoldUntil = 0;
    if (before) showMusic(before, 0);
    setStatus(status, error.message, "error");
    toast(error.message);
  }
  setTimeout(refreshState, 1500);
}

function toggleStation(station, star) {
  const known = (currentConfig.music.radio?.favorites || []).some((item) => item.uuid === station.uuid);
  saveConfig((config) => {
    const list = config.music.radio.favorites;
    config.music.radio.favorites = known ? list.filter((item) => item.uuid !== station.uuid) : [...list, { uuid: station.uuid, name: station.name, url: station.url, country: station.country || "", codec: station.codec || "", bitrate: Number(station.bitrate || 0), tags: station.tags || [] }];
  }, known ? "Aus Lieblingssendern entfernt" : "Als Lieblingssender gemerkt").then((saved) => {
    if (!saved) return;
    star.classList.toggle("is-on", !known);
    if (!known) { star.classList.remove("pop"); void star.offsetWidth; star.classList.add("pop"); }
    renderFavoriteTiles();
  });
}

function nightStartMinutes() {
  const [hours, minutes] = String(runtime?.ui?.night?.start || "23:00").split(":").map(Number);
  let delta = hours * 60 + minutes - minutesOfDay();
  if (delta <= 0) delta += 24 * 60;
  return delta;
}

async function setSleep(key) {
  const active = nowPlaying?.sleep_end && nowPlaying.sleep_label === key;
  const minutes = active ? 0 : key === "night" ? nightStartMinutes() : Number(key);
  try {
    await post("/api/player/sleep", { minutes, label: active ? "" : key });
    toast(minutes ? `Musik geht aus in ${minutes} min` : "Ausschalt-Timer aus");
  } catch (error) {
    toast(error.message);
  }
  refreshState();
}

/* Konten und Lautsprecher */
function spotifyRedirectUri() {
  return `${window.location.origin}/api/music/spotify/callback`;
}

function renderAccounts(pendingYt = false) {
  byId("ytState").textContent = pendingYt ? "Anmeldefenster ist offen. Melde dich dort mit deinem Google-Konto an." : meta.ytConnected ? "Verbunden." : "Nicht verbunden.";
  byId("ytConnect").textContent = meta.ytConnected ? "Neu anmelden" : "Am Bildschirm anmelden";
  byId("ytConnect").hidden = pendingYt || meta.remote;
  byId("ytCancel").hidden = !pendingYt;
  byId("ytDisconnect").hidden = !meta.ytConnected || pendingYt;
  byId("spotifyState").textContent = meta.spotifyConnected ? "Verbunden. Der Zugang erneuert sich automatisch." : "Nicht verbunden. Die Anmeldung öffnet sich auf diesem Gerät und kehrt danach zurück.";
  byId("spotifyConnect").textContent = meta.spotifyConnected ? "Neu verbinden" : "Verbinden";
  byId("spotifyDisconnect").hidden = !meta.spotifyConnected;
  byId("spotifyRedirect").textContent = spotifyRedirectUri();
  byId("spotifyHostHint").hidden = window.location.hostname !== "localhost";
}

async function pollYtLogin(quiet = false) {
  try {
    const status = await api("/api/music/ytmusic/login");
    meta.ytConnected = !!status.connected;
    if (status.status === "pending") {
      renderAccounts(true);
      panelTimers.push(setTimeout(() => pollYtLogin(), 2000));
      return;
    }
    renderAccounts();
    if (quiet) return;
    if (status.status === "connected") { setStatus(byId("ytStatus"), `Verbunden. Zuletzt gehört: ${status.latest || "–"}`, "success"); toast("YouTube Music verbunden"); }
    else if (status.status === "error") setStatus(byId("ytStatus"), status.error || "Anmeldung fehlgeschlagen", "error");
  } catch (error) {
    if (!quiet) setStatus(byId("ytStatus"), `Anmeldestatus nicht abrufbar: ${error.message}`, "error");
  }
}

async function connectYt() {
  hideKeyboard();
  setStatus(byId("ytStatus"), "Anmeldefenster wird geöffnet …");
  try {
    await post("/api/music/ytmusic/login");
    setStatus(byId("ytStatus"), "");
    renderAccounts(true);
    panelTimers.push(setTimeout(() => pollYtLogin(), 2000));
  } catch (error) {
    setStatus(byId("ytStatus"), `Anmelden fehlgeschlagen: ${error.message}`, "error");
  }
}

async function connectSpotify() {
  const clientId = byId("cfgSpotifyClient").value.trim();
  if (!clientId) { setStatus(byId("spotifyStatus"), "Bitte zuerst die Client-ID eintragen (Anleitung unten).", "error"); return; }
  setStatus(byId("spotifyStatus"), "Spotify-Anmeldung wird geöffnet …");
  try {
    const result = await post("/api/music/spotify/authorize", { client_id: clientId, redirect_uri: spotifyRedirectUri() });
    window.location.assign(result.url);
  } catch (error) {
    setStatus(byId("spotifyStatus"), `Verbinden fehlgeschlagen: ${error.message}`, "error");
  }
}

async function confirmAndPost(question, path, done) {
  if (!(await askConfirm(question, "", "Ja", { danger: true }))) return;
  try {
    await post(path);
    done();
  } catch (error) {
    toast(error.message);
  }
}

function renderSpotifyPair(status) {
  const pairing = status.status === "pairing";
  byId("spPairState").textContent = status.paired
    ? status.running ? `Gekoppelt. „${status.name}“ steht in Spotify als Lautsprecher bereit.` : "Gekoppelt, startet gerade …"
    : pairing ? "Warte auf die Bestätigung in Spotify …" : "Nicht gekoppelt. Damit Spotify auf dem Pi spielt, einmal koppeln.";
  byId("spPair").textContent = status.paired ? "Neu koppeln" : "Pi als Spotify-Lautsprecher koppeln";
  byId("spUnpair").hidden = !status.paired;
  byId("spPairBox").hidden = !(pairing && status.qr);
  if (pairing && status.qr) {
    byId("spPairQr").src = status.qr;
    byId("spPairUrl").textContent = status.url;
    byId("spPairCode").textContent = status.code || "–";
  }
  if (status.status === "error" || status.status === "crashed") setStatus(byId("spPairStatus"), status.error || "Der Spotify-Lautsprecher ist abgestürzt", "error");
}

async function loadSpotifyPair(fromPairing = false) {
  try {
    const status = await api("/api/music/spotify/pair");
    renderSpotifyPair(status);
    if (status.status === "pairing" && !byId("sheet").hidden) panelTimers.push(setTimeout(() => loadSpotifyPair(true), 2000));
    else if (fromPairing && status.paired) { setStatus(byId("spPairStatus"), "Gekoppelt", "success"); toast("Spotify-Lautsprecher gekoppelt"); }
  } catch (error) {
    setStatus(byId("spPairStatus"), `Status nicht abrufbar: ${error.message}`, "error");
  }
}

const BT_ACTIONS = { scan: "Suche läuft", connect: "Verbinde", disconnect: "Trenne", remove: "Entferne" };
function renderBluetooth(status) {
  byId("btScan").disabled = !status.available || status.busy;
  const linked = (kind) => (status.devices || []).filter((device) => device.connected && device.kind === kind).map((device) => device.name);
  const connected = linked("audio");
  const pads = linked("gamepad");
  byId("mOutName").textContent = connected[0] || "Lautsprecher des Pi";
  if (!status.available) {
    byId("btState").textContent = "";
    setStatus(byId("btStatus"), status.error || "Bluetooth nicht verfügbar", "error");
    byId("btDevices").replaceChildren();
    return;
  }
  byId("btState").textContent = [connected.length ? `Ton läuft über: ${connected.join(", ")}` : "Kein Lautsprecher verbunden.",
    pads.length ? `Controller: ${pads.join(", ")}` : ""].filter(Boolean).join(" · ");
  if (status.busy) {
    const name = status.devices.find((device) => device.mac === status.mac)?.name || "";
    setStatus(byId("btStatus"), `${BT_ACTIONS[status.action] || "Bitte warten"}${name ? ` mit ${name}` : ""} …`);
  } else if (status.message) {
    setStatus(byId("btStatus"), status.message, status.ok ? "success" : "error");
  } else {
    setStatus(byId("btStatus"), status.devices.length ? "" : "Noch keine Geräte gefunden. „Geräte suchen“ tippen.");
  }
  byId("btDevices").replaceChildren(...status.devices.map((device) => {
    const row = el("div", "row is-plain bt-row");
    const text = el("div");
    const kind = device.kind === "gamepad" ? "Controller · " : device.kind === "audio" ? "Lautsprecher · " : "";
    text.append(el("strong", "", device.name), el("span", "sub", ` · ${kind}${device.connected ? "verbunden" : device.paired ? "gekoppelt" : "gefunden"}`));
    const actions = el("div", "acts");
    const list = device.connected ? [["disconnect", "Trennen", "button-secondary"]] : [["connect", "Verbinden", "button-primary"]];
    if (device.paired) list.push(["remove", "Entfernen", "button-danger"]);
    list.forEach(([action, label, style]) => {
      const button = el("button", `button ${style}`, label);
      button.type = "button";
      button.disabled = status.busy;
      button.addEventListener("click", () => bluetoothAction(action, device.mac));
      actions.append(button);
    });
    row.append(text, actions);
    return row;
  }));
}

async function loadBluetooth(poll = true) {
  try {
    const status = await api("/api/bluetooth");
    renderBluetooth(status);
    if (poll && status.busy && !byId("sheet").hidden) panelTimers.push(setTimeout(() => loadBluetooth(true), 1500));
  } catch (error) {
    setStatus(byId("btStatus"), `Bluetooth-Status nicht abrufbar: ${error.message}`, "error");
  }
}

async function bluetoothAction(action, mac = "") {
  if (action === "remove" && !(await askConfirm("Gerät entfernen?", "Danach muss es neu gekoppelt werden.", "Entfernen", { danger: true }))) return;
  try {
    await post(`/api/bluetooth/${action}`, { mac });
  } catch (error) {
    setStatus(byId("btStatus"), error.message, "error");
    return;
  }
  loadBluetooth(true);
}

/* ---------- System ---------- */
function renderDiag() {
  byId("diag").tBodies[0].replaceChildren(...sourceStates().map((source) => {
    const row = el("tr");
    row.append(el("td", "", source.name), el("td", source.state, `● ${source.text}`), el("td", "", source.last), el("td", "", source.limit));
    return row;
  }));
}

async function loadSystemInfo() {
  try {
    const info = await api("/api/system/info");
    meta.backlight = !!info.backlight;
    const uptime = Math.floor(info.uptime_seconds / 60);
    byId("sysInfo").textContent = `Version ${info.version} · läuft seit ${Math.floor(uptime / 60)} Std. ${uptime % 60} Min.${info.config_saved ? ` · Konfiguration zuletzt gespeichert ${ago(info.config_saved)}` : ""}`;
    applyBacklightHint();
  } catch (error) {
    byId("sysInfo").textContent = error.message;
  }
  panelTimers.push(setTimeout(() => { if (isPage("system")) { renderDiag(); loadSystemInfo(); } }, 5000));
}

function applyBacklightHint() {
  const input = byId("nightOff");
  input.disabled = !meta.backlight && !input.checked;
  byId("nightOffHint").textContent = meta.backlight ? "über die Hintergrundbeleuchtung des Displays" : "nicht möglich: das Display lässt sich von hier nicht schalten; es wird nur gedimmt";
}

/* ---------- Am Handy (Kopplung) ---------- */
let pairEnd = 0;
async function startPairing(fresh = false) {
  try {
    const status = fresh || !pairEnd || pairEnd < Date.now() ? await post("/api/pair/start") : await api("/api/pair/status");
    renderPairing(status);
  } catch (error) {
    byId("qr").replaceChildren(el("span", "hint", error.message));
  }
}

function renderPairing(status) {
  pairEnd = status.active ? Date.now() + status.expires_in * 1000 : 0;
  const code = status.code || "";
  byId("pairCode").textContent = code ? `${code.slice(0, 3)} ${code.slice(3)}` : "––– –––";
  const box = byId("qr");
  if (status.qr) {
    const image = el("img");
    image.alt = "";
    image.src = status.qr;
    box.replaceChildren(image);
  } else {
    box.replaceChildren(el("span", "hint", "Adresse unten eintragen, dann erscheint hier der QR-Code"));
  }
  byId("pairStepUrl").textContent = status.url ? `QR-Code scannen oder ${status.url} öffnen.` : "QR-Code scannen oder die Adresse des Pi im Browser öffnen.";
  byId("pairDocker").textContent = status.url ? "" : "Im Docker-Container kennt der Pi seine Adresse im Heimnetz nicht. Trag sie oben ein (z. B. http://raspberrypi.local:8080) und gib den Port im Heimnetz frei: in der .env-Datei STORCHCAM_BIND=0.0.0.0 setzen und den Container neu starten.";
  tickPairing(false);
}

function tickPairing(claimed = false) {
  if (!isPage("phone")) return;
  if (claimed) {
    pairEnd = 0;
    byId("pairCode").textContent = "––– –––";
    byId("pairLeft").textContent = "Handy gekoppelt · für ein weiteres Handy einen neuen Code erzeugen";
    toast("Handy gekoppelt");
    return;
  }
  const left = Math.max(0, pairEnd - Date.now()) / 1000;
  byId("pairLeft").textContent = left ? `gültig noch ${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}` : "abgelaufen";
  if (!left) return;
  panelTimers.push(setTimeout(async () => {
    if (!isPage("phone")) return;
    try {
      if ((await api("/api/pair/status")).claimed) { tickPairing(true); return; }
    } catch (_error) { /* nächster Versuch */ }
    tickPairing();
  }, 1000));
}

/* ---------- Anmeldung: PIN am Bildschirm, Kopplungscode am Handy ---------- */
let authMode = "login";
function openAuth(status) {
  const dialog = byId("pinDialog");
  authMode = status.remote && !(status.pin_login && status.pin_configured && status.pin_required) ? "pair" : status.pin_configured ? "login" : "setup";
  const pin = byId("adminPin");
  byId("pinEyebrow").textContent = authMode === "pair" ? "Am Handy einstellen" : "Menü";
  byId("pinTitle").textContent = authMode === "pair" ? "Code vom Bildschirm eingeben" : authMode === "setup" ? "Admin-PIN erstmals festlegen" : "Admin-PIN eingeben";
  byId("pinHelp").textContent = authMode === "pair" ? "Am Bildschirm: Menü → „Am Handy“ zeigt einen sechsstelligen Code, 10 Minuten gültig." : authMode === "setup" ? "Die PIN wird nur als sicherer Hash auf diesem Gerät gespeichert." : "Die Sitzung endet automatisch nach der eingestellten Zeit.";
  byId("pinLabel").textContent = authMode === "pair" ? "Kopplungscode" : "PIN";
  pin.autocomplete = authMode === "pair" ? "one-time-code" : "current-password";
  pin.type = authMode === "pair" ? "text" : "password";
  byId("pinConfirmField").hidden = authMode !== "setup";
  byId("adminPinConfirm").required = authMode === "setup";
  byId("submitPin").textContent = authMode === "pair" ? "Koppeln" : authMode === "setup" ? "PIN festlegen" : "Anmelden";
  pin.value = "";
  byId("adminPinConfirm").value = "";
  setStatus(byId("pinStatus"), "");
  if (!dialog.open) dialog.showModal();
  pin.focus();
}

async function authenticate(event) {
  event.preventDefault();
  const pin = byId("adminPin").value.replace(/\s+/g, "");
  if (authMode === "setup" && pin !== byId("adminPinConfirm").value.trim()) { setStatus(byId("pinStatus"), "Die PINs stimmen nicht überein.", "error"); return; }
  const submit = byId("submitPin");
  submit.disabled = true;
  setStatus(byId("pinStatus"), authMode === "pair" ? "Wird geprüft …" : "Anmeldung läuft …");
  try {
    const path = authMode === "pair" ? "/api/pair/claim" : authMode === "setup" ? "/api/admin/setup" : "/api/admin/login";
    const result = await post(path, authMode === "pair" ? { code: pin } : { pin });
    token = result.token;
    sessionStorage.setItem("storchcam-admin-token", token);
    await loadConfig();
    byId("pinDialog").close();
    hideKeyboard();
    openSheet(menuTab);
  } catch (error) {
    setStatus(byId("pinStatus"), error.message, "error");
  } finally {
    submit.disabled = false;
  }
}

async function claimFromLink() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("koppeln");
  if (!code) return false;
  window.history.replaceState(null, "", window.location.pathname);
  try {
    const result = await post("/api/pair/claim", { code });
    token = result.token;
    sessionStorage.setItem("storchcam-admin-token", token);
    await adminStatus();
    await loadConfig();
    openSheet("home");
    toast("Gekoppelt · das Menü steht jetzt am Handy zur Verfügung");
  } catch (error) {
    await adminStatus().then(openAuth).catch(() => {});
    setStatus(byId("pinStatus"), error.message, "error");
  }
  return true;
}

/* ---------- Einrichtung beim ersten Start ---------- */
const wizard = { step: 0, stream: null, place: null, stops: [], results: { live: [], places: [], stops: [] } };
function openWizard() {
  wizard.step = 0;
  renderWizard();
  if (!byId("wizard").open) byId("wizard").showModal();
  if (!wizard.results.live.length) wizardSearch();
}

function wizardSearchRow(id, label, value, placeholder) {
  const row = el("div", "search-row");
  const field = el("label", "field");
  field.htmlFor = id;
  const input = el("input");
  input.id = id;
  input.type = "search";
  input.value = value;
  input.placeholder = placeholder;
  input.dataset.enter = "wzSearchBtn";
  field.append(el("span", "", label), input);
  const button = el("button", "button button-secondary", "Suchen");
  button.type = "button";
  button.id = "wzSearchBtn";
  button.addEventListener("click", () => wizardSearch());
  row.append(field, button);
  return row;
}

function renderWizard() {
  $$("#wzSteps i").forEach((bar, index) => bar.classList.toggle("is-done", index <= wizard.step));
  const body = byId("wzBody");
  byId("wzBack").hidden = wizard.step === 0;
  byId("wzSkip").textContent = wizard.step === 2 ? "Überspringen" : "Später einrichten";
  byId("wzNext").textContent = wizard.step === 2 ? "Fertig" : "Weiter";
  byId("wzNext").disabled = wizard.step === 0 && !wizard.stream;
  if (wizard.step === 0) {
    byId("wzTitle").textContent = "Welche Kamera soll laufen?";
    byId("wzText").textContent = "Alles, was gerade auf YouTube live ist. Später kannst du Favoriten als Ersatz festlegen.";
    const results = el("div", "results");
    wizard.results.live.forEach((video) => {
      const card = liveCard({ ...video, url: video.embed_url }, (picked) => { wizard.stream = picked; renderWizard(); }, { star: false });
      card.classList.toggle("is-current", wizard.stream?.embed_url === video.embed_url);
      results.append(card);
    });
    body.replaceChildren(wizardSearchRow("wzQuery", "Suchbegriff", byId("wzQuery")?.value || "Live Cam", "z. B. Storch, Hafen, Zoo"), el("p", "status-message", ""), results);
  } else if (wizard.step === 1) {
    byId("wzTitle").textContent = "Wo steht der Bildschirm?";
    byId("wzText").textContent = "Für Uhrzeit und Wetter. Die Kamera darf woanders sein.";
    const picks = wizard.results.places.map((place) => {
      const pick = el("button", "pick");
      pick.type = "button";
      pick.setAttribute("aria-pressed", String(wizard.place?.name === place.name && wizard.place?.latitude === place.latitude));
      const text = el("div");
      text.append(el("strong", "", place.name), el("span", "", [place.region, place.timezone].filter(Boolean).join(" · ")));
      pick.append(text);
      pick.addEventListener("click", () => { wizard.place = place; renderWizard(); });
      return pick;
    });
    body.replaceChildren(wizardSearchRow("wzPlace", "Ort suchen", wizard.place?.name || runtime?.location?.label || "", "z. B. Berlin"), el("p", "status-message", ""), ...picks);
  } else {
    byId("wzTitle").textContent = "Abfahrten in der Nähe?";
    byId("wzText").textContent = "Optional. Derzeit für Haltestellen im VRR (Nordrhein-Westfalen). Die Gehzeit stellst du später im Menü ein.";
    const picks = wizard.results.stops.map((station) => {
      const chosen = wizard.stops.some((item) => item.station_id === station.station_id);
      const pick = el("button", "pick");
      pick.type = "button";
      pick.setAttribute("aria-pressed", String(chosen));
      const text = el("div");
      text.append(el("strong", "", station.station_name), el("span", "", "VRR"));
      pick.append(text, el("span", "", chosen ? "hinzugefügt" : "hinzufügen"));
      pick.addEventListener("click", () => {
        wizard.stops = chosen ? wizard.stops.filter((item) => item.station_id !== station.station_id) : [...wizard.stops, station];
        renderWizard();
      });
      return pick;
    });
    body.replaceChildren(wizardSearchRow("wzStop", "Haltestelle suchen", byId("wzStop")?.value || "", "z. B. Düsseldorf Hauptbahnhof"), el("p", "status-message", ""), ...picks);
  }
}

async function wizardSearch() {
  const button = byId("wzSearchBtn");
  hideKeyboard();
  busyButton(button, true);
  try {
    if (wizard.step === 0) {
      const query = byId("wzQuery")?.value.trim() || "Live Cam";
      wizard.results.live = (await api(`/api/live/search?q=${encodeURIComponent(query)}`)).results;
      if (!wizard.results.live.length) throw new Error("Gerade ist dazu nichts live. Anderes Suchwort versuchen.");
    } else if (wizard.step === 1) {
      const query = byId("wzPlace").value.trim();
      if (query.length < 2) throw new Error("Bitte mindestens zwei Zeichen eingeben.");
      wizard.results.places = (await api(`/api/location/search?q=${encodeURIComponent(query)}`)).results;
      if (!wizard.results.places.length) throw new Error("Kein Ort gefunden.");
    } else {
      const query = byId("wzStop").value.trim();
      if (query.length < 2) throw new Error("Bitte mindestens zwei Zeichen eingeben.");
      wizard.results.stops = (await api(`/api/station/search?q=${encodeURIComponent(query)}`)).results;
      if (!wizard.results.stops.length) throw new Error("Keine VRR-Haltestelle gefunden.");
    }
    renderWizard();
    stagger($("#wzBody .results") || byId("wzBody"));
  } catch (error) {
    renderWizard();
    setStatus($("#wzBody .status-message"), error.message, "error");
  } finally {
    busyButton(byId("wzSearchBtn"), false);
  }
}

async function finishWizard(skipped) {
  const zone = wizard.place ? await timezoneFor(wizard.place) : "";
  const saved = await saveConfig((config) => {
    config.ui.setup_done = true;
    if (skipped) return;
    if (wizard.stream) {
      config.stream.url = wizard.stream.embed_url;
      if (!config.stream.favorites.some((item) => item.url === wizard.stream.embed_url)) config.stream.favorites.unshift({ url: wizard.stream.embed_url, title: wizard.stream.title, channel: wizard.stream.channel });
      titles.set(wizard.stream.embed_url, { title: wizard.stream.title, channel: wizard.stream.channel });
    }
    if (wizard.place) {
      config.location = { label: wizard.place.name, latitude: Number(wizard.place.latitude), longitude: Number(wizard.place.longitude) };
      if (zone) config.app.timezone = zone;
    }
    wizard.stops.forEach((station) => {
      if (!config.transit.stops.some((stop) => stop.station_id === station.station_id)) config.transit.stops.push(stopFrom(station));
    });
    if (wizard.stops.length) config.ui.transit.enabled = true;
  }, skipped ? "Einrichtung später" : "Einrichtung abgeschlossen", { undo: false });
  if (saved) byId("wizard").close();
}

async function maybeStartWizard() {
  if (runtime?.ui?.setup_done || runtime?.stream?.url) return;
  try {
    const status = await adminStatus();
    if (status.remote || !status.authenticated) return;
    await loadConfig();
    openWizard();
  } catch (_error) { /* ohne Menüzugang keine Einrichtung */ }
}

/* ---------- Bildschirmtastatur (nur am Kiosk selbst) ---------- */
const OSK_LAYOUTS = {
  letters: [
    ["q", "w", "e", "r", "t", "z", "u", "i", "o", "p", "ü"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", "ö", "ä"],
    ["shift", "y", "x", "c", "v", "b", "n", "m", "ß", "backspace"],
    ["mode", "-", "space", ".", "enter"],
  ],
  numbers: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["@", "/", ":", ";", "(", ")", "&", "+", "'", "\""],
    ["_", ",", "?", "!", "#", "%", "=", "backspace"],
    ["mode", "-", "space", ".", "enter"],
  ],
};
const OSK_ICONS = {
  shift: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 4 12h4.5v7h7v-7H20z"/></svg>',
  backspace: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5h11v14H9l-6-7z"/><path d="m12.5 9.5 5 5m0-5-5 5"/></svg>',
};
const OSK_LABELS = { shift: "Großbuchstabe", backspace: "Löschen", space: "Leerzeichen" };
const osk = { target: null, shift: false, layout: "letters" };
const oskUpper = (key) => (key === "ß" ? key : key.toLocaleUpperCase("de-DE"));
const TYPABLE = new Set(["text", "search", "password", "number", "url", "textarea"]);

function buildKeyboard() {
  const handle = el("button", "osk-close", "Tastatur schließen");
  handle.type = "button";
  handle.dataset.key = "hide";
  const rows = OSK_LAYOUTS[osk.layout].map((keys) => {
    const row = el("div", "osk-row");
    keys.forEach((key) => {
      const button = el("button", `osk-key osk-${key.length === 1 ? "char" : key}`);
      button.type = "button";
      button.dataset.key = key;
      if (OSK_ICONS[key]) button.innerHTML = OSK_ICONS[key];
      else if (key === "mode") button.textContent = osk.layout === "letters" ? "123" : "ABC";
      else if (key === "enter") button.textContent = osk.target?.dataset.enter ? "Los" : "OK";
      else if (key === "space") button.textContent = "Leerzeichen";
      else button.textContent = osk.shift ? oskUpper(key) : key;
      if (OSK_LABELS[key]) button.setAttribute("aria-label", OSK_LABELS[key]);
      if (key === "shift") button.setAttribute("aria-pressed", String(osk.shift));
      row.append(button);
    });
    return row;
  });
  byId("osk").replaceChildren(handle, ...rows);
}

function setShift(value) {
  osk.shift = value;
  $$(".osk-char", byId("osk")).forEach((button) => { button.textContent = osk.shift ? oskUpper(button.dataset.key) : button.dataset.key; });
  $(".osk-shift", byId("osk"))?.setAttribute("aria-pressed", String(osk.shift));
}

function showKeyboard(input) {
  if (meta.remote || !TYPABLE.has(input.type)) return;
  osk.target = input;
  osk.layout = input.type === "number" || input.inputMode === "numeric" ? "numbers" : "letters";
  osk.shift = osk.layout === "letters" && input.type !== "password" && input.type !== "url" && !input.value;
  buildKeyboard();
  const keyboard = byId("osk");
  const host = input.closest(".dialog-card, .wz") || document.body;
  if (keyboard.parentElement !== host) host.append(keyboard);
  keyboard.hidden = false;
  byId("sheet").classList.toggle("osk-open", host === document.body);
  input.scrollIntoView({ block: "center", behavior: "auto" });
}

function hideKeyboard() {
  osk.target = null;
  byId("osk").hidden = true;
  byId("sheet").classList.remove("osk-open");
}

function typeKey(key) {
  const input = osk.target;
  if (!input) return;
  if (key === "hide") { hideKeyboard(); return; }
  if (key === "shift") { setShift(!osk.shift); return; }
  if (key === "mode") { osk.layout = osk.layout === "letters" ? "numbers" : "letters"; osk.shift = false; buildKeyboard(); return; }
  if (key === "enter") {
    const target = input.dataset.enter && byId(input.dataset.enter);
    hideKeyboard();
    if (target) target.click();
    else if (input.form) input.form.requestSubmit();
    return;
  }
  const character = key === "space" ? " " : osk.shift ? oskUpper(key) : key;
  const caret = input.type !== "number" && input.type !== "url" && typeof input.selectionStart === "number";
  if (key === "backspace") {
    if (caret && input.selectionStart !== input.selectionEnd) input.setRangeText("", input.selectionStart, input.selectionEnd, "end");
    else if (caret && input.selectionStart > 0) input.setRangeText("", input.selectionStart - 1, input.selectionStart, "end");
    else if (!caret) input.value = input.value.slice(0, -1);
  } else if (caret) {
    input.setRangeText(character, input.selectionStart, input.selectionEnd, "end");
  } else {
    input.value += character;
  }
  if (osk.shift && key !== "backspace") setShift(false);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

/* ---------- Gaming: RetroArch als Container, verwaltet über den Hilfsdienst auf dem Pi ---------- */
const gm = { status: null, path: "", folder: null, chosen: "", jobTimer: null, uploading: false };
const GM_STEP_ICON = { pending: "○", running: "", done: "✓", error: "!", skipped: "–" };

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1).replace(".", ",")} GB`;
  if (value >= 1024 ** 2) return `${Math.round(value / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}

function showGamingView(name) {
  $$("#gamingBody > .gm-view").forEach((view) => {
    const show = view.dataset.gm === name;
    if (show && view.hidden && !reduceMotion) view.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 300, easing: "cubic-bezier(.2,.9,.25,1)" });
    view.hidden = !show;
  });
}

async function loadGaming() {
  if (!gm.status) showGamingView("loading");
  try {
    gm.status = await api("/api/gaming/status");
  } catch (error) {
    gm.status = null;
    byId("gmDockerError").textContent = error.message;
    showGamingView("docker");
    return;
  }
  renderGaming();
}

function gamingView(status) {
  if (!status.agent) return "agent";
  if (!status.docker?.ok) return "docker";
  if (status.job?.state === "running") return "job";
  if (status.container) return "dash";
  return "setup";
}

function renderGaming() {
  const status = gm.status;
  const view = gamingView(status);
  byId("subGaming").textContent = view === "dash" ? (status.container.running ? "Läuft" : "Gestoppt") : view === "setup" ? "Noch nicht eingerichtet" : "RetroArch im Browser";
  if (view === "agent") {
    byId("gmAgentCmd").textContent = status.install_command || "";
    byId("gmAgentError").textContent = status.agent_error || "";
  }
  if (view === "docker") byId("gmDockerError").textContent = status.docker?.error || "";
  if (view === "setup") renderCandidates(status.candidates || []);
  if (view === "job") { renderJob(status.job); pollJob(); }
  if (view === "dash") renderGamingDash(status);
  showGamingView(view);
}

function renderCandidates(candidates) {
  byId("gmCandidates").replaceChildren(...candidates.map((item) => {
    const row = el("div", "row");
    const text = el("div");
    text.append(el("strong", "", `Vorhandenes RetroArch: ${item.name}`), el("span", "sub", `${item.status || item.state} · ${item.image}`));
    const adopt = el("button", "button button-secondary", "Übernehmen");
    adopt.type = "button";
    adopt.addEventListener("click", async () => {
      if (!(await askConfirm(`„${item.name}“ übernehmen?`, "StorchCam zeigt den Container dann an und kann ihn starten und stoppen. Er wird nicht neu erstellt und nicht verändert.", "Übernehmen"))) return;
      try { gm.status = { ...gm.status, ...(await post("/api/gaming/adopt", { name: item.name, confirm: true })) }; toast("Übernommen"); loadGaming(); }
      catch (error) { toast(error.message); }
    });
    row.append(icon("i-gamepad"), text, adopt);
    return row;
  }));
}

/* Ordnerwahl auf dem Pi: nur freigegebene Ordner, mit freiem Speicher und Schreibrechten */
async function openFolder(path) {
  const box = byId("gmFolders");
  setStatus(byId("gmFolderStatus"), "");
  skeletons(box, 3);
  try {
    const folder = await api(`/api/gaming/folders?path=${encodeURIComponent(path)}`);
    gm.path = folder.path;
    gm.folder = folder;
    renderFolder(folder);
  } catch (error) {
    box.replaceChildren();
    setStatus(byId("gmFolderStatus"), error.message, "error");
  }
}

function folderRow(label, info, onClick, { up = false, gaming = false } = {}) {
  const row = el("button", "row-link");
  row.type = "button";
  const iconBox = el("span", `tile-icon${gaming ? " is-gaming" : ""}`);
  iconBox.append(icon(up ? "i-back" : gaming ? "i-gamepad" : "i-folder"));
  const text = el("span");
  text.append(el("strong", "", label), el("small", "", info));
  row.append(iconBox, text, icon("i-chev"));
  row.lastChild.classList.add("chev");
  row.addEventListener("click", onClick);
  return row;
}

function renderFolder(folder) {
  const box = byId("gmFolders");
  const atRoots = !folder.path;
  byId("gmHere").textContent = atRoots ? "Freigegebene Ordner" : folder.path;
  byId("gmHereInfo").textContent = atRoots ? "Wähle, wo Spiele und Spielstände liegen sollen" : `${formatBytes(folder.free_bytes)} frei · ${folder.writable ? "beschreibbar" : "keine Schreibrechte"}${folder.gaming ? " · enthält schon Gaming-Daten" : ""}`;
  byId("gmChoose").hidden = atRoots || !folder.writable;
  byId("gmNewRow").hidden = atRoots || !folder.writable;
  const rows = [];
  if (!atRoots) rows.push(folderRow(folder.parent ? "Übergeordneter Ordner" : "Zur Auswahl", folder.parent || "freigegebene Ordner", () => openFolder(folder.parent || ""), { up: true }));
  const entries = atRoots ? folder.roots.map((root) => ({ name: root.path, path: root.path, writable: root.writable, info: `${formatBytes(root.free_bytes)} frei` })) : folder.entries;
  entries.forEach((entry) => rows.push(folderRow(entry.name, entry.info || (entry.gaming ? "Gaming-Daten vorhanden" : entry.writable ? "Ordner" : "nur lesbar"), () => openFolder(entry.path), { gaming: entry.gaming })));
  if (!atRoots && !folder.entries.length) rows.push(el("p", "hint", "Keine Unterordner. Hier einen neuen Ordner anlegen oder diesen nehmen."));
  box.replaceChildren(...rows);
  stagger(box);
}

async function chooseFolder() {
  if (!gm.folder?.path) return;
  gm.chosen = gm.folder.path;
  byId("gmReviewPath").textContent = gm.chosen;
  byId("gmReviewSpace").textContent = `${formatBytes(gm.folder.free_bytes)} frei${gm.folder.free_bytes < 2 * 1024 ** 3 ? " · knapp, mindestens 2 GB empfohlen" : ""}`;
  setStatus(byId("gmReviewStatus"), "");
  showGamingView("review");
  checkPort();
}

async function checkPort() {
  const port = Number(byId("gmPort").value);
  const info = byId("gmPortInfo");
  if (!(port >= 1024 && port <= 65535)) { info.textContent = "bitte 1024 bis 65535"; return false; }
  info.textContent = "wird geprüft …";
  try {
    const result = await api(`/api/gaming/ports?port=${port}`);
    info.textContent = result.free ? "frei" : "belegt, bitte einen anderen wählen";
    info.classList.toggle("is-bad", !result.free);
    return result.free;
  } catch (error) {
    info.textContent = error.message;
    return false;
  }
}

async function startInstall() {
  const port = Number(byId("gmPort").value);
  if (!(await checkPort())) { setStatus(byId("gmReviewStatus"), "Der Port ist nicht frei.", "error"); return; }
  if (!(await askConfirm("Gaming jetzt installieren?", `RetroArch wird geladen und als Container „storchcam-gaming“ auf Port ${port} gestartet. Speicherort: ${gm.chosen}.`, "Installieren"))) return;
  try {
    const job = await post("/api/gaming/install", { folder: gm.chosen, port, confirm: true });
    gm.status = { ...gm.status, job };
    renderJob(job);
    showGamingView("job");
    pollJob();
  } catch (error) {
    setStatus(byId("gmReviewStatus"), error.message, "error");
  }
}

const JOB_TITLES = { install: ["Installation läuft", "Gaming ist installiert", "Installation angehalten"], update: ["Update läuft", "Update fertig", "Update angehalten"], rollback: ["Vorige Version wird gestartet", "Vorige Version läuft", "Zurückstellen angehalten"] };

function renderJob(job) {
  if (!job) return;
  const [running, done, failed] = JOB_TITLES[job.kind] || JOB_TITLES.install;
  byId("gmJobTitle").textContent = job.state === "running" ? running : job.state === "done" ? done : failed;
  byId("gmJobProgress").hidden = job.kind !== "install";
  byId("gmSteps").replaceChildren(...job.steps.map((step) => {
    const item = el("li", `gm-step is-${step.status}`);
    const mark = el("span", "gm-mark", GM_STEP_ICON[step.status] || "");
    const text = el("span", "gm-step-text");
    text.append(el("strong", "", step.label), el("small", "", step.detail || ""));
    item.append(mark, text);
    return item;
  }));
  setStatus(byId("gmJobStatus"), job.state === "error" ? `${job.error} · Angelegte Ordner und Daten bleiben erhalten; es wurde nichts gelöscht.` : job.state === "done" ? "Fertig." : "", job.state === "error" ? "error" : job.state === "done" ? "success" : "");
  byId("gmJobDone").hidden = job.state === "running";
}

function pollJob() {
  clearTimeout(gm.jobTimer);
  gm.jobTimer = setTimeout(async () => {
    if (!isPage("gaming")) return;
    try {
      const { job } = await api("/api/gaming/job");
      renderJob(job);
      if (job?.state === "running") { pollJob(); return; }
      if (job?.state === "done") { toast(job.kind === "install" ? "Gaming ist eingerichtet" : "Erledigt"); gm.status = null; loadGaming(); }
    } catch (error) {
      setStatus(byId("gmJobStatus"), error.message, "error");
      pollJob();
    }
  }, 1000);
}

function renderGamingDash(status) {
  const box = status.container;
  const running = box.running;
  const reachable = box.reachable;
  byId("gmStateTitle").textContent = running ? (reachable ? "Gaming läuft" : "Gaming startet …") : box.state === "exited" ? "Gaming ist gestoppt" : `Zustand: ${box.state}`;
  byId("gmStateSub").textContent = [box.name, box.version ? `Version ${box.version}` : "", box.port ? `Port ${box.port}` : ""].filter(Boolean).join(" · ");
  byId("gmStatePill").replaceChildren(el("span", `dot${running && reachable ? "" : running ? " is-warn" : " is-bad"}`), document.createTextNode(running ? (reachable ? "erreichbar" : "nicht erreichbar") : "aus"));
  byId("gmStateIcon").classList.toggle("is-on", running && reachable);
  const toggle = byId("gmToggle");
  toggle.replaceChildren(icon(running ? "i-close" : "i-play"), document.createTextNode(running ? "Stoppen" : "Starten"));
  byId("gmOpen").disabled = !running;
  byId("gmOpen").replaceChildren(icon(onPiScreen() ? "i-phone" : "i-play"), document.createTextNode(onPiScreen() ? "Auf Handy oder Laptop öffnen" : "Gaming öffnen"));
  renderScreenCard();
  watchScreen();
  byId("gmPause").checked = !!status.settings?.pause_camera;
  const setup = status.setup || {};
  const facts = [
    ["Container", `${box.name} · ${box.state}`],
    ["Image", box.image],
    ["Version", box.version || "unbekannt"],
    ["Speicherort", setup.folder || (box.mounts.find((mount) => mount.target === "/config")?.source ?? "–")],
    ["Docker", status.docker.version],
  ];
  byId("gmFacts").replaceChildren(...facts.map(([label, value]) => {
    const cell = el("div", "cell");
    cell.append(el("span", "", label), el("strong", "gm-value", value || "–"));
    return cell;
  }));
  byId("gmUpdate").hidden = !!setup.adopted;
  byId("gmRollback").hidden = !setup.previous || !!setup.adopted;
  if (running) loadAccess(); else byId("gmAccess").hidden = true;
  loadRoms();
  if (running && !reachable) panelTimers.push(setTimeout(() => { if (isPage("gaming")) loadGaming(); }, 5000));
}

// Am Pi-Bildschirm öffnet sich kein neues Fenster (der Kiosk hätte keinen Weg zurück);
// dort wird über „Am Pi-Bildschirm spielen“ mit RetroArch direkt gespielt.
const onPiScreen = () => !meta.remote;

async function loadAccess() {
  try {
    const access = await api("/api/gaming/access");
    byId("gmQr").src = access.qr;
    byId("gmUrl").href = access.url;
    byId("gmUrl").textContent = access.url;
    const others = (access.urls || []).filter((url) => url !== access.url);
    byId("gmUrlAlt").hidden = !others.length;
    byId("gmUrlAlt").textContent = others.length ? `oder ${others.join(" · ")}` : "";
    byId("gmAccessLead").textContent = onPiScreen() ? "Oder auf einem anderen Gerät im Heimnetz spielen: QR-Code mit Handy oder Tablet scannen, oder am Laptop öffnen:" : "Auf diesem Gerät oder einem anderen im Heimnetz öffnen:";
    byId("gmUser").textContent = access.user || "–";
    byId("gmPass").textContent = access.password || "–";
    byId("gmAccess").hidden = false;
  } catch (_error) {
    byId("gmAccess").hidden = true;
  }
}

async function loadRoms() {
  try {
    const roms = await api("/api/gaming/roms");
    byId("gmRomFolder").textContent = `Ordner auf dem Pi: ${roms.folder}`;
    byId("gmRomCount").textContent = roms.files.length ? `${roms.files.length} Dateien` : "";
    const box = byId("gmRoms");
    if (!roms.files.length) { box.replaceChildren(el("p", "hint", "Noch keine Spiele vorhanden. Füge deine ROM-Dateien hinzu.")); return; }
    const groups = new Map();
    roms.files.forEach((file) => groups.set(file.system, [...(groups.get(file.system) || []), file]));
    const nodes = [];
    groups.forEach((files, system) => {
      nodes.push(el("div", "group-head", `${system} · ${files.length}`));
      files.forEach((file) => {
        const row = el("div", "mrow");
        const text = el("span", "txt");
        text.append(el("strong", "", file.name), el("span", "sub", formatBytes(file.size)));
        const art = el("span", "art");
        art.style.setProperty("--h", String(hue(system)));
        art.append(icon("i-gamepad"));
        row.append(art, text, el("span"));
        nodes.push(row);
      });
    });
    box.replaceChildren(...nodes);
  } catch (error) {
    byId("gmRoms").replaceChildren(el("p", "hint", error.message));
  }
}

// Spiele hochladen: Datei für Datei mit echtem Fortschritt (XMLHttpRequest kennt den Upload-Fortschritt).
function uploadRom(file, row) {
  return new Promise((resolve) => {
    const request = new XMLHttpRequest();
    request.open("PUT", `/api/gaming/roms?name=${encodeURIComponent(file.name)}`);
    if (token) request.setRequestHeader("Authorization", `Bearer ${token}`);
    const bar = $("i", row);
    const note = $(".sub", row);
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      bar.style.width = `${(event.loaded / event.total) * 100}%`;
      note.textContent = `${formatBytes(event.loaded)} von ${formatBytes(event.total)}`;
    });
    request.addEventListener("load", () => {
      let answer = {};
      try { answer = JSON.parse(request.responseText); } catch (_error) { /* egal */ }
      const ok = request.status >= 200 && request.status < 300;
      row.classList.add(ok ? "is-done" : "is-error");
      note.textContent = ok ? `fertig · ${answer.system || ""}` : answer.error || `Fehler ${request.status}`;
      resolve(ok);
    });
    request.addEventListener("error", () => { row.classList.add("is-error"); note.textContent = "Verbindung abgebrochen"; resolve(false); });
    request.send(file);
  });
}

async function uploadRoms(files) {
  if (!files.length || gm.uploading) return;
  gm.uploading = true;
  const box = byId("gmUploads");
  const rows = files.map((file) => {
    const row = el("div", "gm-upload");
    const text = el("div");
    text.append(el("strong", "", file.name), el("span", "sub", "wartet …"));
    const track = el("span", "np-track");
    track.append(el("i"));
    row.append(text, track);
    return row;
  });
  box.replaceChildren(...rows);
  let ok = 0;
  for (const [index, file] of files.entries()) if (await uploadRom(file, rows[index])) ok += 1;
  gm.uploading = false;
  toast(`${ok} von ${files.length} Spielen hinzugefügt`);
  loadRoms();
}

async function toggleGaming() {
  const running = gm.status?.container?.running;
  const button = byId("gmToggle");
  busyButton(button, true);
  try {
    gm.status = { ...gm.status, ...(await post("/api/gaming/control", { action: running ? "stop" : "start" })) };
    toast(running ? "Gaming gestoppt" : "Gaming startet");
    renderGaming();
  } catch (error) {
    toast(error.message);
  } finally {
    busyButton(button, false);
  }
}

async function startUpdate(rollback) {
  const question = rollback ? ["Vorige Version starten?", "Der Container wird mit der gesicherten vorigen Version neu erstellt. Spiele und Spielstände bleiben erhalten.", "Vorige Version"]
    : ["Nach Update suchen und installieren?", "Lädt die neueste Version von linuxserver. Gibt es eine neue, wird die bisherige gesichert und der Container neu erstellt. Spiele, Spielstände und Einstellungen bleiben erhalten.", "Update starten"];
  if (!(await askConfirm(...question))) return;
  try {
    const job = await post("/api/gaming/update", { rollback, confirm: true });
    gm.status = { ...gm.status, job };
    renderJob(job);
    showGamingView("job");
    pollJob();
  } catch (error) {
    toast(error.message);
  }
}

// Bestätigen per großem Auswahlblatt statt winzigem Browser-Dialog.
function askConfirm(title, text, label, { danger = false } = {}) {
  return new Promise((resolve) => {
    let answered = false;
    const yes = el("button", `button ${danger ? "button-danger" : "button-primary"}`, label);
    yes.type = "button";
    yes.addEventListener("click", () => { answered = true; closeAction(); resolve(true); });
    openAction(title, "", [el("p", "hint", text), yes], () => { if (!answered) resolve(false); });
  });
}

/* ---------- Gaming am Pi-Bildschirm (HDMI) ----------
   Der Hilfsdienst startet RetroArch direkt im Vollbild auf dem Bildschirm des Pi (über dem Kiosk).
   Controller hängen am Pi (USB oder Bluetooth); beendet wird in RetroArch oder hier im Menü. */
const SCREEN_COMMAND = "sudo ./scripts/gaming/install-agent.sh --screen";

function screenHint(screen, setup) {
  if (!screen) return ["Dafür braucht der Gaming-Hilfsdienst ein Update. Im StorchCam-Ordner auf dem Pi ausführen:", SCREEN_COMMAND];
  if (!screen.installed || !screen.cores) {
    return [screen.installed ? "Es sind noch keine Emulator-Kerne installiert. Im StorchCam-Ordner auf dem Pi ausführen:"
      : "Einmalig RetroArch und Emulator-Kerne auf dem Pi installieren. Im StorchCam-Ordner ausführen:", screen.install_command || SCREEN_COMMAND];
  }
  if (!setup?.folder) return ["Geht nur mit einem Speicherordner, den StorchCam eingerichtet hat.", ""];
  if (screen.running) return ["RetroArch läuft im Vollbild am Pi-Bildschirm, die Kamera im Hintergrund. Zurück zur Kamera: in RetroArch „Beenden“ wählen oder hier beenden.", ""];
  if (!screen.display) return ["Kein Bildschirm gefunden: Die Desktop-Sitzung am Pi läuft nicht oder gehört einem anderen Benutzer als der Gaming-Hilfsdienst.", ""];
  const failed = screen.last_exit?.error ? ` Der letzte Start ist fehlgeschlagen: ${screen.last_exit.error.split("\n").slice(-2).join(" ")}` : "";
  return [`RetroArch startet im Vollbild auf dem Bildschirm am Pi (${screen.cores} Emulator-Kerne). Controller per USB anstecken oder per Bluetooth koppeln; die Kamera läuft im Hintergrund weiter.${failed}`, ""];
}

function renderScreenCard() {
  const screen = gm.status?.screen;
  const setup = gm.status?.setup || {};
  const [hint, command] = screenHint(screen, setup);
  byId("gmScreenSub").textContent = hint;
  byId("gmScreenCmd").textContent = command;
  byId("gmScreenCmd").hidden = !command;
  const running = !!screen?.running;
  const button = byId("gmScreen");
  button.hidden = !screen || !screen.installed || !screen.cores || !setup.folder;
  button.disabled = !running && !screen?.display;
  button.classList.toggle("button-primary", !running);
  button.classList.toggle("button-secondary", running);
  button.replaceChildren(icon(running ? "i-close" : "i-tv"), document.createTextNode(running ? "Am Pi-Bildschirm beenden" : "Am Pi-Bildschirm spielen"));
}

// Solange RetroArch am Bildschirm läuft und die Gaming-Seite offen ist: Zustand nachziehen (z. B. in RetroArch beendet).
function watchScreen() {
  if (!gm.status?.screen?.running) return;
  panelTimers.push(setTimeout(async () => {
    if (!isPage("gaming")) return;
    try {
      gm.status = { ...gm.status, screen: await api("/api/gaming/screen") };
      renderScreenCard();
    } catch (_error) { /* nächster Versuch beim nächsten Öffnen */ }
    watchScreen();
  }, 4000));
}

async function toggleScreen() {
  const running = !!gm.status?.screen?.running;
  if (running && !(await askConfirm("Spiel am Bildschirm beenden?", "RetroArch wird beendet und der Bildschirm zeigt wieder die Kamera. Was das Spiel selbst gespeichert hat, bleibt; Fortschritt seit dem letzten Speichern geht verloren.", "Beenden"))) return;
  const button = byId("gmScreen");
  busyButton(button, true);
  try {
    const screen = await post("/api/gaming/screen", { action: running ? "stop" : "start" });
    gm.status = { ...gm.status, screen };
    toast(running ? "Zurück zur Kamera" : onPiScreen() ? "RetroArch startet …" : "RetroArch startet am Pi-Bildschirm");
    // Am Bildschirm selbst das Menü schließen: Nach dem Spielen erscheint wieder die Kamera.
    if (!running && onPiScreen()) closeSheet();
    else watchScreen();
  } catch (error) {
    toast(error.message);
  } finally {
    busyButton(button, false);
    renderScreenCard();
  }
}

/* Kamera anhalten, solange Gaming läuft (einstellbar im Gaming-Menü) */
function gamingPaused() {
  return !!(runtime?.gaming?.pause_camera && lastState?.gaming?.running);
}

/* ---------- Auswahl wie am iPhone: große Zeilen in einem Blatt statt winziger Dropdowns ---------- */
let actionClose = null;

function openAction(title, sub, nodes, onClose = null) {
  const box = byId("action");
  byId("actionTitle").textContent = title;
  byId("actionSub").textContent = sub || "";
  byId("actionBody").replaceChildren(...nodes);
  actionClose = onClose;
  box.hidden = false;
  if (reduceMotion) return;
  $(".action-backdrop", box).animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
  $(".action-card", box).animate([{ transform: "translateY(40px) scale(.97)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 380, easing: "cubic-bezier(.34,1.3,.64,1)" });
}

function closeAction() {
  const box = byId("action");
  if (box.hidden) return;
  const done = () => { box.hidden = true; if (actionClose) { const after = actionClose; actionClose = null; after(); } };
  if (reduceMotion) { done(); return; }
  $(".action-backdrop", box).animate([{ opacity: 1 }, { opacity: 0 }], { duration: 180, easing: "ease-in" });
  $(".action-card", box).animate([{ transform: "none", opacity: 1 }, { transform: "translateY(30px) scale(.98)", opacity: 0 }], { duration: 180, easing: "cubic-bezier(.4,0,1,1)" }).finished.then(done).catch(done);
}

function fieldLabel(input) {
  const caption = input.closest("label")?.querySelector(":scope > span");
  return caption?.firstChild?.textContent?.trim() || "";
}

function pickerButton(input, open) {
  const button = el("button", "picker");
  button.type = "button";
  button.append(el("span", "picker-value"), icon("i-chev"));
  button.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); open(); });
  input.classList.add("is-replaced");
  input.tabIndex = -1;
  input.after(button);
  // Die ganze Zeile ist die Tippfläche, nicht nur der Wert rechts.
  input.closest("label")?.addEventListener("click", (event) => { if (!event.target.closest(".picker")) { event.preventDefault(); open(); } });
  return button;
}

function enhancePickers(root = document) {
  $$("select:not([data-picker])", root).forEach((select) => {
    select.dataset.picker = "list";
    pickerButton(select, () => openListPicker(select));
    select.addEventListener("change", () => syncPicker(select));
  });
  $$('input[type="time"]:not([data-picker])', root).forEach((input) => {
    input.dataset.picker = "time";
    pickerButton(input, () => openTimePicker(input));
    input.addEventListener("change", () => syncPicker(input));
  });
  syncPickers();
}

function syncPicker(input) {
  const value = input.nextElementSibling?.classList.contains("picker") ? $(".picker-value", input.nextElementSibling) : null;
  if (!value) return;
  value.textContent = input.dataset.picker === "time" ? `${input.value || "–"} Uhr` : input.selectedOptions?.[0]?.textContent || "–";
}

function syncPickers() {
  $$("[data-picker]").forEach(syncPicker);
}

function choose(input, value) {
  if (input.value !== value) {
    input.value = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
  syncPicker(input);
}

function openListPicker(select) {
  const rows = [...select.options].map((option) => {
    const row = el("button", "pick-row");
    row.type = "button";
    row.setAttribute("aria-pressed", String(option.selected));
    row.append(el("span", "", option.textContent), icon("i-check"));
    row.addEventListener("click", () => {
      $$(".pick-row", byId("actionBody")).forEach((other) => other.setAttribute("aria-pressed", String(other === row)));
      setTimeout(() => { closeAction(); choose(select, option.value); }, reduceMotion ? 0 : 140);
    });
    return row;
  });
  openAction(fieldLabel(select), select.closest("label")?.querySelector("small")?.textContent || "", rows);
}

function openTimePicker(input) {
  const [hours, minutes] = (input.value || "00:00").split(":").map(Number);
  const time = { hours, minutes: Math.round(minutes / 5) * 5 % 60 };
  const show = el("output", "time-show");
  const render = () => { show.textContent = `${String(time.hours).padStart(2, "0")}:${String(time.minutes).padStart(2, "0")}`; };
  const wheel = (key, step, limit, label) => {
    const column = el("div", "time-col");
    const make = (delta, iconName, text) => {
      const button = el("button", "round-btn");
      button.type = "button";
      button.append(icon(iconName));
      button.setAttribute("aria-label", `${label} ${text}`);
      button.addEventListener("click", () => { time[key] = (time[key] + delta + limit) % limit; render(); });
      return button;
    };
    column.append(make(step, "i-up", "mehr"), el("span", "hint", label), make(-step, "i-down", "weniger"));
    return column;
  };
  const presets = el("div", "chips");
  ["21:00", "22:00", "23:00", "00:00", "06:00", "07:00"].forEach((preset) => {
    const chip = el("button", "chip", preset);
    chip.type = "button";
    chip.addEventListener("click", () => { [time.hours, time.minutes] = preset.split(":").map(Number); render(); });
    presets.append(chip);
  });
  const done = el("button", "button button-primary", "Übernehmen");
  done.type = "button";
  done.addEventListener("click", () => { closeAction(); choose(input, show.textContent); });
  const dial = el("div", "time-dial");
  dial.append(wheel("hours", 1, 24, "Stunde"), show, wheel("minutes", 5, 60, "Minute"));
  render();
  openAction(fieldLabel(input), "Uhrzeit wählen", [dial, presets, done]);
}

$$("[data-action-close]").forEach((node) => node.addEventListener("click", closeAction));

/* ---------- Bedienung ---------- */

let idleTimer = null;
function wake() {
  byId("menuBtn").classList.remove("is-idle");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => byId("menuBtn").classList.add("is-idle"), 8000);
  armCompact();
}
["pointerdown", "keydown"].forEach((name) => window.addEventListener(name, wake, { passive: true }));

let swipe = null;
let swallowUntil = 0;
byId("dock").addEventListener("pointerdown", (event) => { swipe = { y: event.clientY, x: event.clientX }; });
byId("dock").addEventListener("pointerup", (event) => {
  if (!swipe) return;
  const dy = event.clientY - swipe.y;
  const dx = Math.abs(event.clientX - swipe.x);
  swipe = null;
  if (Math.abs(dy) < 40 || dx > Math.abs(dy)) return;
  swallowUntil = Date.now() + 350;
  if (dy > 0) {
    setBar(barMode === "full" ? "compact" : "hidden");
    toast(barMode === "hidden" ? "Leiste ausgeblendet · unten antippen zum Zurückholen" : "Leiste verkleinert");
  } else if (barMode !== "full") {
    setBar("full");
  } else {
    openDetail("depart");
  }
});
const tapOk = () => Date.now() > swallowUntil;
byId("minibar").addEventListener("click", () => { if (tapOk()) { setBar("full"); armCompact(); } });
byId("barPeek").addEventListener("click", () => { setBar("full"); armCompact(); });
byId("barHandle").addEventListener("click", (event) => { event.stopPropagation(); if (tapOk()) setBar("compact"); });
[["transitBlock", "depart"], ["weatherBlock", "weather"]].forEach(([id, kind]) => {
  byId(id).addEventListener("click", () => { if (tapOk()) openDetail(kind); });
  byId(id).addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetail(kind); } });
});
byId("warning").addEventListener("click", () => openDetail("warning"));
$$("[data-close]").forEach((button) => button.addEventListener("click", () => closeDetails()));
elements.playerBox.addEventListener("click", () => {
  if (!byId("sheet").hidden && window.innerWidth <= 900) { closeSheet(); return; }
  closeDetails();
  if (!byId("camCard").hidden) byId("streamChip").click();
  else showChip(6000);
});
byId("night").addEventListener("click", wakeFromNight);
byId("streamReload").addEventListener("click", () => { refreshState(); if (runtime?.stream) configureStream(runtime.stream, true); });

byId("streamChip").addEventListener("click", (event) => {
  event.stopPropagation();
  const card = byId("camCard");
  const open = card.hidden;
  if (open) renderCamCard();
  card.hidden = !open;
  byId("streamChip").setAttribute("aria-expanded", String(open));
  if (!open) showChip(4000);
});

byId("npOpen").addEventListener("click", () => setCardOpen(!byId("nowPlaying").classList.contains("is-open")));
// Lautstärke direkt auf der kleinen Karte: Lautsprecher antippen öffnet den Regler.
byId("npVolBtn").addEventListener("click", (event) => {
  event.stopPropagation();
  const card = byId("nowPlaying");
  const show = !card.classList.contains("is-vol");
  resizeCard((node) => node.classList.toggle("is-vol", show));
  byId("npVolBtn").setAttribute("aria-expanded", String(show));
  clearTimeout(cardTimer);
  if (show) cardTimer = setTimeout(() => setCardOpen(false), 8000);
});
$$("[data-vol-step]").forEach((button) => button.addEventListener("click", (event) => {
  event.stopPropagation();
  const slider = byId("npVol");
  const value = Math.max(0, Math.min(100, Number(slider.value) + Number(button.dataset.volStep)));
  setVolumeSliders(value);
  sendControl("volume", value);
  keepCardOpen();
}));

/* ---------- Popups: Antippen schließt sie, ein Tipp daneben auch ---------- */
function dismissToast() {
  const box = byId("toast");
  if (box.hidden) return;
  clearTimeout(toastTimer);
  if (reduceMotion) { box.hidden = true; return; }
  box.animate([{ opacity: 1, transform: "translate(-50%, 0)" }, { opacity: 0, transform: "translate(-50%, 18px) scale(.96)" }], { duration: 200, easing: "cubic-bezier(.4,0,1,1)" })
    .finished.then(() => { box.hidden = true; }).catch(() => {});
}

function hideWithFade(node) {
  if (node.hidden) return;
  if (reduceMotion) { node.hidden = true; return; }
  node.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-6px) scale(.98)" }], { duration: 180, easing: "ease-in" })
    .finished.then(() => { node.hidden = true; }).catch(() => {});
}

byId("toast").addEventListener("click", (event) => { if (!event.target.closest("#toastUndo")) dismissToast(); });
// Wischen schiebt den Hinweis mit dem Finger weg, wie eine Mitteilung am iPhone.
(() => {
  const box = byId("toast");
  let start = null;
  box.addEventListener("pointerdown", (event) => { if (!event.target.closest("#toastUndo")) start = { x: event.clientX, y: event.clientY }; });
  box.addEventListener("pointermove", (event) => {
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = Math.max(0, event.clientY - start.y);
    box.style.transform = `translate(calc(-50% + ${dx}px), ${dy}px)`;
    box.style.opacity = String(Math.max(0.2, 1 - (Math.abs(dx) + dy) / 220));
  });
  const end = (event) => {
    if (!start) return;
    const moved = Math.abs(event.clientX - start.x) + Math.max(0, event.clientY - start.y);
    start = null;
    box.style.transform = "";
    box.style.opacity = "";
    if (moved > 60) { clearTimeout(toastTimer); box.hidden = true; }
  };
  box.addEventListener("pointerup", end);
  box.addEventListener("pointercancel", end);
})();
$$(".detail").forEach((detail) => detail.addEventListener("click", () => closeDetails()));
byId("camCard").addEventListener("click", (event) => { event.stopPropagation(); byId("streamChip").click(); });
["fallbackNote", "signalLost"].forEach((id) => byId(id).addEventListener("click", () => hideWithFade(byId(id))));
document.addEventListener("pointerdown", (event) => {
  const card = byId("nowPlaying");
  if ((card.classList.contains("is-open") || card.classList.contains("is-vol")) && !event.target.closest("#nowPlaying")) setCardOpen(false);
  if (!byId("camCard").hidden && !event.target.closest("#camCard, #streamChip")) byId("streamChip").click();
  if (!byId("toast").hidden && !event.target.closest("#toast, #sheet")) dismissToast();
}, { passive: true });
$$("[data-mctl]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); sendControl(button.dataset.mctl); }));

elements.video.addEventListener("playing", () => {
  player.retries = 0;
  clearTimeout(player.retryTimer);
  player.retryTimer = null;
  const was = player.reconnecting;
  player.reconnecting = false;
  releaseFrame();
  if (was) { showReconnect("Wieder stabil", "Kein Bild verpasst · der Puffer füllt sich", true); setTimeout(hideReconnect, 3500); }
  else hideReconnect();
  const title = player.source?.title;
  setStreamView("playing", `${player.source?.kind === "file" ? "Video läuft in Schleife" : "Livestream läuft im eigenen Player"}${title ? `: ${title}` : ""}`);
  showChip();
});
elements.video.addEventListener("error", () => {
  if (player.source?.kind === "file") onPlayerError({ fatal: true, type: "file" }, player.generation);
});
// Niemand soll das Kamerabild anhalten können; pausiert es trotzdem, läuft es sofort weiter.
elements.video.addEventListener("pause", () => { if (videoActive() && player.source) playVideo(); });
elements.live.addEventListener("load", () => {
  if (elements.live.getAttribute("src") && elements.live.src !== "about:blank") setStreamView("embedded", "Stream-Seite geladen");
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !player.source) return;
  playVideo();
  const live = player.hls?.liveSyncPosition;
  if (Number.isFinite(live) && elements.video.currentTime < live - 15) elements.video.currentTime = live;
});

/* Menü */
byId("menuBtn").addEventListener("click", () => beginAdmin("home"));

window.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "s") { event.preventDefault(); beginAdmin("home"); }
  if (event.key === "Escape") { if (!byId("action").hidden) closeAction(); else if (!byId("osk").hidden) hideKeyboard(); else if (nav.stack.length > 1 && !byId("sheet").hidden) popPage(); else if (!byId("sheet").hidden) closeSheet(); closeDetails(); }
  if (event.key === "Enter" && event.target.dataset?.enter) { event.preventDefault(); byId(event.target.dataset.enter)?.click(); }
});
byId("qualSel").addEventListener("change", (event) => { const value = event.target.value; saveConfig((config) => { config.stream.quality = value; }, `Bildqualität ${event.target.selectedOptions[0].textContent}`); });
byId("fpsSel").addEventListener("change", (event) => { const value = Number(event.target.value); saveConfig((config) => { config.stream.max_fps = value; }, `Bildrate ${value} Bilder/s`); });
byId("camAudio").addEventListener("change", (event) => { const value = event.target.value; saveConfig((config) => { config.stream.audio = value; }, `Ton der Kamera: ${event.target.selectedOptions[0].textContent}`); });
byId("autoFav").addEventListener("change", (event) => { const value = event.target.checked; saveConfig((config) => { config.stream.auto_fallback = value; }, value ? "Automatischer Wechsel an" : "Automatischer Wechsel aus"); });
byId("liveSearchBtn").addEventListener("click", () => searchLive(byId("liveQuery").value.trim()));
byId("streamUrlSave").addEventListener("click", () => {
  const url = byId("cfgStream").value.trim();
  hideKeyboard();
  player.override = null;
  saveConfig((config) => { config.stream.url = url; }, url ? "Eigene Adresse" : "Stream entfernt");
});
byId("reloadStreamPanel").addEventListener("click", () => { if (runtime?.stream) configureStream(runtime.stream, true); toast("Stream wird neu geladen"); });
byId("placeSearchBtn").addEventListener("click", () => searchPlaces(byId("placeQuery"), byId("placeResults"), byId("placeStatus"), choosePlace, byId("placeSearchBtn")));
byId("skySel").addEventListener("change", (event) => { const value = event.target.value; saveConfig((config) => { config.ui.sky = value; }, "Tag/Nacht"); });
byId("distSeg").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-dist]");
  if (!button || Number(button.dataset.dist) === Number(currentConfig?.ui.distance)) return;
  const value = Number(button.dataset.dist);
  saveConfig((config) => { config.ui.distance = value; }, `Leseabstand ${button.textContent}`);
});
$$("[data-block]").forEach((input) => input.addEventListener("change", () => {
  const key = input.dataset.block;
  const value = input.checked;
  saveConfig((config) => { config.ui[key].enabled = value; }, `${input.parentElement.firstChild.textContent.trim()} ${value ? "an" : "aus"}`);
}));
byId("compactSel").addEventListener("change", (event) => { const value = Number(event.target.value); saveConfig((config) => { config.ui.compact_after = value; }, "Leiste verkleinern"); });
byId("warnSel").addEventListener("change", (event) => { const value = event.target.value; saveConfig((config) => { config.weather.warnings = value; }, `Unwetterwarnungen: ${event.target.selectedOptions[0].textContent}`); });
byId("nightOn").addEventListener("change", (event) => { const value = event.target.checked; saveConfig((config) => { config.ui.night.enabled = value; }, value ? "Nachtmodus an" : "Nachtmodus aus"); });
byId("nightOff").addEventListener("change", (event) => { const value = event.target.checked; saveConfig((config) => { config.ui.night.screen_off = value; }, value ? "Nachts ganz aus" : "Nachts gedimmt"); });
[["nightFrom", "start", "Beginn"], ["nightTo", "end", "Ende"]].forEach(([id, key, label]) => byId(id).addEventListener("change", (event) => {
  const value = event.target.value;
  if (/^\d\d:\d\d$/.test(value)) saveConfig((config) => { config.ui.night[key] = value; }, `Nachtmodus ${label} ${value}`);
}));
byId("leaveMode").addEventListener("change", (event) => { const value = event.target.checked; saveConfig((config) => { config.transit.leave_mode = value; }, value ? "Losgeh-Zeit an" : "Losgeh-Zeit aus"); });
byId("stopSearchBtn").addEventListener("click", () => searchStops(byId("stopQuery"), byId("stopResults"), byId("stopStatus"), addStop, byId("stopSearchBtn")));

byId("srcSeg").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-src]");
  if (!button || button.dataset.src === music.src) return;
  music.src = button.dataset.src;
  music.filter = "";
  music.results = [];
  music.playlists = [];
  music.searched = false;
  byId("mQuery").value = "";
  setStatus(byId("mStatus"), "");
  renderMusicPanel();
  if (music.src === "radio") searchMusic(true); else loadPlaylists();
  stagger(byId("mFavs"));
});
byId("mSearch").addEventListener("click", () => searchMusic());
VOLUME_SLIDERS.forEach((id) => {
  const slider = byId(id);
  slider.addEventListener("input", () => { setVolumeSliders(Number(slider.value)); keepCardOpen(); });
  slider.addEventListener("change", () => sendControl("volume", Number(slider.value)));
});
byId("mOut").addEventListener("click", () => {
  goTo("accounts");
  setTimeout(() => byId("btScan").scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" }), 480);
});
byId("ppSeek").addEventListener("input", (event) => {
  pp.seeking = true;
  const duration = Number(nowPlaying?.duration_ms || 0);
  const position = (Number(event.target.value) / 1000) * duration;
  event.target.style.setProperty("--p", `${Number(event.target.value) / 10}%`);
  byId("ppPos").textContent = formatDuration(position);
  byId("ppDur").textContent = `-${formatDuration(duration - position)}`;
});
byId("ppSeek").addEventListener("change", (event) => {
  const duration = Number(nowPlaying?.duration_ms || 0);
  const position = (Number(event.target.value) / 1000) * duration;
  pp.seeking = false;
  sendControl("seek", Math.round(position / 1000));
});
byId("ppShuffle").addEventListener("click", () => {
  const on = !nowPlaying?.shuffle;
  toast(on ? "Zufällige Reihenfolge an" : "Zufällige Reihenfolge aus");
  sendControl("shuffle", on);
});
byId("ppRepeat").addEventListener("click", () => {
  const mode = REPEAT_NEXT[nowPlaying?.repeat || "off"];
  toast(REPEAT_LABEL[mode]);
  sendControl("repeat", mode);
});
byId("ppLike").addEventListener("click", toggleLike);
byId("ppAdd").addEventListener("click", toggleAddList);
byId("ppToMusic").addEventListener("click", () => goTo("music"));
$$("[data-gm-reload]").forEach((button) => button.addEventListener("click", () => { gm.status = null; loadGaming(); }));
byId("gmStart").addEventListener("click", () => { showGamingView("wizard"); openFolder(""); });
byId("gmChoose").addEventListener("click", chooseFolder);
byId("gmBackToFolders").addEventListener("click", () => { showGamingView("wizard"); openFolder(gm.path); });
byId("gmMkdir").addEventListener("click", async () => {
  const name = byId("gmNewName").value.trim();
  hideKeyboard();
  try { const made = await post("/api/gaming/folders", { path: gm.path, name }); toast(`Ordner „${name}“ angelegt`); openFolder(made.path); }
  catch (error) { setStatus(byId("gmFolderStatus"), error.message, "error"); }
});
byId("gmPort").addEventListener("change", checkPort);
byId("gmInstall").addEventListener("click", startInstall);
byId("gmToggle").addEventListener("click", toggleGaming);
byId("gmScreen").addEventListener("click", toggleScreen);
byId("gmPads").addEventListener("click", () => {
  goTo("accounts");
  setTimeout(() => byId("btScan").scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" }), 480);
});
byId("gmUrl").addEventListener("click", (event) => { if (onPiScreen()) event.preventDefault(); });
byId("gmOpen").addEventListener("click", () => {
  const card = byId("gmAccess");
  if (onPiScreen()) {
    // Kein Fenster im Kiosk öffnen: zum QR-Code springen und ihn kurz hervorheben.
    card.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
    if (!reduceMotion) card.animate([{ boxShadow: "0 0 0 0 transparent" }, { boxShadow: "0 0 0 6px color-mix(in srgb, var(--accent) 55%, transparent)" }, { boxShadow: "0 0 0 0 transparent" }], { duration: 1400, easing: "ease-in-out" });
    return;
  }
  const url = byId("gmUrl").href;
  if (url) window.open(url, "_blank", "noopener");
});
byId("gmUpload").addEventListener("click", () => byId("gmFile").click());
byId("gmFile").addEventListener("change", (event) => { uploadRoms([...event.target.files]); event.target.value = ""; });
byId("gmRomsReload").addEventListener("click", loadRoms);
byId("gmUpdate").addEventListener("click", () => startUpdate(false));
byId("gmRollback").addEventListener("click", () => startUpdate(true));
byId("gmPause").addEventListener("change", (event) => {
  const value = event.target.checked;
  saveConfig((config) => { config.gaming = { ...(config.gaming || {}), pause_camera: value }; }, value ? "Kamera hält beim Spielen an" : "Kamera läuft beim Spielen weiter")
    .then((saved) => { if (saved && gm.status) gm.status.settings = { pause_camera: value }; });
});
byId("toBrowse").addEventListener("click", () => goTo("music"));
byId("miniOpen").addEventListener("click", () => goTo("player"));
byId("homeOpen").addEventListener("click", () => goTo("player"));
SLEEP_GROUPS.forEach((id) => byId(id).addEventListener("click", (event) => { const chip = event.target.closest(".chip"); if (chip) setSleep(chip.dataset.sleep); }));
byId("cfgMusicSource").addEventListener("change", (event) => { const value = event.target.value; saveConfig((config) => { config.music.source = value; }, "Songkarte"); });
byId("ytConnect").addEventListener("click", connectYt);
byId("ytCancel").addEventListener("click", async () => { try { await post("/api/music/ytmusic/cancel"); } catch (_error) { /* egal */ } renderAccounts(); });
byId("ytDisconnect").addEventListener("click", () => confirmAndPost("YouTube Music trennen?", "/api/music/ytmusic/disconnect", () => { meta.ytConnected = false; renderAccounts(); toast("YouTube Music getrennt"); }));
byId("spotifyConnect").addEventListener("click", connectSpotify);
byId("spotifyDisconnect").addEventListener("click", () => confirmAndPost("Spotify-Verbindung trennen?", "/api/music/spotify/disconnect", () => { meta.spotifyConnected = false; renderAccounts(); toast("Spotify getrennt"); }));
byId("spPair").addEventListener("click", async () => {
  setStatus(byId("spPairStatus"), "Kopplungscode wird angefordert …");
  try { renderSpotifyPair(await post("/api/music/spotify/pair")); setStatus(byId("spPairStatus"), ""); panelTimers.push(setTimeout(() => loadSpotifyPair(true), 2000)); }
  catch (error) { setStatus(byId("spPairStatus"), error.message, "error"); }
});
byId("spUnpair").addEventListener("click", () => confirmAndPost("Spotify-Kopplung des Pi entfernen?", "/api/music/spotify/unpair", () => loadSpotifyPair()));
byId("btScan").addEventListener("click", () => bluetoothAction("scan"));

byId("sysSave").addEventListener("click", () => {
  const key = byId("cfgYoutubeKey").value.trim();
  const query = byId("cfgYoutubeQuery").value.trim();
  hideKeyboard();
  saveConfig((config) => { config.youtube.api_key = key; config.youtube.default_query = query; }, key ? "YouTube-Schlüssel" : "Suchwort").then((saved) => {
    if (saved && key) { meta.youtubeKeySet = true; byId("cfgYoutubeKey").value = ""; syncSheet(); }
  });
});
byId("wizardAgain").addEventListener("click", () => { closeSheet(); openWizard(); });
byId("pairNew").addEventListener("click", () => { startPairing(true); toast("Neuer Kopplungscode erzeugt"); });
byId("publicUrlSave").addEventListener("click", () => {
  const value = byId("publicUrl").value.trim();
  hideKeyboard();
  saveConfig((config) => { config.server.public_url = value; }, "Adresse im Heimnetz").then((saved) => { if (saved) startPairing(true); });
});
byId("lanOn").addEventListener("change", (event) => { const value = event.target.checked; saveConfig((config) => { config.server.lan_access = value; }, value ? "PIN-Anmeldung im Heimnetz an" : "PIN-Anmeldung im Heimnetz aus"); });

byId("wzNext").addEventListener("click", () => { if (wizard.step < 2) { wizard.step += 1; renderWizard(); } else finishWizard(false); });
byId("wzBack").addEventListener("click", () => { wizard.step = Math.max(0, wizard.step - 1); renderWizard(); });
byId("wzSkip").addEventListener("click", () => { if (wizard.step === 2) { wizard.stops = []; finishWizard(false); } else finishWizard(true); });
byId("wizard").addEventListener("cancel", (event) => event.preventDefault());

byId("pinForm").addEventListener("submit", authenticate);
byId("cancelPin").addEventListener("click", () => { hideKeyboard(); byId("pinDialog").close(); });
byId("pinDialog").addEventListener("close", hideKeyboard);
document.addEventListener("focusin", (event) => {
  const input = event.target;
  if ((input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) && input.closest(".sheet, .wz, .dialog-card")) showKeyboard(input);
});
byId("osk").addEventListener("pointerdown", (event) => {
  const key = event.target.closest("[data-key]");
  if (!key) return;
  event.preventDefault();
  typeKey(key.dataset.key);
});
byId("osk").addEventListener("mousedown", (event) => event.preventDefault());
byId("osk").addEventListener("click", (event) => {
  const key = event.target.closest("[data-key]");
  if (key && event.detail === 0) typeKey(key.dataset.key);
});

// Rücksprung von der Spotify-Anmeldung: Meldung zeigen und die Musik wieder öffnen.
function handleMusicReturn() {
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("music");
  if (!outcome) return false;
  window.history.replaceState(null, "", window.location.pathname);
  toast(outcome === "connected" ? "Spotify verbunden" : `Spotify: ${params.get("reason") || "Anmeldung fehlgeschlagen"}`);
  beginAdmin("music");
  return true;
}

/* ---------- Start ---------- */
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    computeLayout();
    applyBlocks();
    renderWeather();
    renderBoards();
    refreshPills();
    if (!byId("detailWeather").hidden) renderWeatherChart();
  }, 140);
});
new ResizeObserver(() => { if (booted && lastState?.weather?.ok) renderStrip(lastState.weather); }).observe(byId("weatherBlock"));

(async function start() {
  buildThemeGrid();
  setupGestures();
  enhancePickers();
  setBar("full", true);
  renderClock();
  await refreshState();
  try { await adminStatus(); } catch (_error) { /* Status kommt beim nächsten Menüaufruf */ }
  booted = true;
  wake();
  if (!(await claimFromLink()) && !handleMusicReturn()) maybeStartWizard();
  setInterval(() => { if (refreshDue()) refreshState(); }, 500);
  setInterval(() => { renderClock(); updateMusicProgress(); applyNight(); applyTheme(); }, 1000);
  setInterval(watchPlayer, 2000);
  setInterval(() => { const go = renderBoards(); if (go) expandFor(60000); }, 15000);
  // Einbrennschutz: die Leiste wandert alle 3 Minuten um einen Pixel.
  let shift = 0;
  setInterval(() => {
    shift = (shift + 1) % 4;
    const [x, y] = [[0, 0], [1, 0], [1, 1], [0, 1]][shift];
    byId("dock").style.transform = `translate(${x}px, ${y}px)`;
  }, 180000);
  if (document.fonts?.ready) document.fonts.ready.then(() => { applyBlocks(); renderWeather(); });
})();
