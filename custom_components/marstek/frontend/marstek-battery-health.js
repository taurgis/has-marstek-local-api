// Marstek battery health cards and the optional sidebar dashboard.
// The integration loads this module itself; no Lovelace resource is needed.
// Custom cards: https://contributors.home-assistant.io/frontend/custom-ui/card/
// Strategies:   https://contributors.home-assistant.io/frontend/custom-ui/custom-strategy/
// Tabs follow the WAI-ARIA pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

const HEALTH_CARD = "marstek-battery-health-card";
const FLEET_CARD = "marstek-battery-fleet-card";
const EDITOR = "marstek-battery-health-card-editor";
const STRATEGY = "marstek-battery-health";
const TABS = ["capacity", "runs", "energy"];
const DOCS = "https://github.com/taurgis/has-marstek-local-api/blob/main/docs/usable-capacity.md";
// Health bands. They describe fade, not a warranty: the Open API carries no warranty terms.
const GOOD_HEALTH = 90;
const WORN_HEALTH = 80;
const MIN_TREND_DAYS = 28;
const DAY_S = 86400;

// Same ?v= token as this module, so a cached copy of the text never
// outlives an update. The backend hashes both files into that token.
const { COPY } = await import(`./marstek-battery-health-copy.js${new URL(import.meta.url).search}`);
const { plugPowerId, plugFlow } = await import(`./marstek-battery-health-plug.js${new URL(import.meta.url).search}`);

const STYLES = `
  :host { display: block; }
  ha-card { padding: 16px; display: flex; flex-direction: column; gap: 16px; color: var(--primary-text-color); }
  .num { font-variant-numeric: tabular-nums; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
  button:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 6px; }
  .top { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 10px; min-width: 0; }
  .top ha-icon { color: var(--state-icon-color, var(--secondary-text-color)); flex: none; }
  .name { margin: 0; font-size: 16px; font-weight: 500; line-height: 24px; flex: 1 1 8em; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .flow { display: inline-flex; margin-left: auto; align-items: center; gap: 4px; font-size: 13px; color: var(--secondary-text-color); white-space: nowrap; }
  .flow ha-icon { --mdc-icon-size: 18px; }
  .lead { margin: 0; font-size: 20px; line-height: 28px; font-weight: 400; }
  .lead strong { font-weight: 600; }
  .band { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 500; margin-right: 6px; vertical-align: 2px; }
  .lead .band { display: flex; width: fit-content; margin: 0 0 6px; padding: 2px 10px 2px 8px; border-radius: 999px;
    font-size: 12px; line-height: 18px; background: color-mix(in srgb, currentColor 12%, transparent); }
  .band ha-icon { --mdc-icon-size: 16px; }
  .band.good { color: var(--success-color, #43a047); }
  .band.fading { color: var(--warning-color, #ffa600); }
  .band.worn { color: var(--error-color, #db4437); }
  .band.measuring { color: var(--secondary-text-color); }
  .cap { display: flex; flex-direction: column; gap: 6px; }
  .cell { position: relative; display: flex; height: 22px; border-radius: 6px; overflow: hidden;
    box-shadow: inset 0 0 0 1.5px var(--secondary-text-color); background: var(--card-background-color, transparent); }
  .cell::after { content: ""; position: absolute; right: -1px; top: 6px; bottom: 6px; width: 4px; border-radius: 0 2px 2px 0; background: var(--secondary-text-color); }
  .seg { height: 100%; }
  .seg.stored { background: var(--primary-color); }
  .seg.room { background: color-mix(in srgb, var(--primary-color) 22%, transparent); }
  .seg.faded { background: repeating-linear-gradient(135deg, color-mix(in srgb, var(--secondary-text-color) 45%, transparent) 0 2px, transparent 2px 6px); }
  .keys { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0; padding: 0; list-style: none; font-size: 12px; color: var(--secondary-text-color); }
  .keys li { display: inline-flex; align-items: center; gap: 6px; }
  .key { width: 10px; height: 10px; border-radius: 2px; flex: none; box-shadow: inset 0 0 0 1px var(--divider-color); }
  .key.stored { background: var(--primary-color); }
  .key.room { background: color-mix(in srgb, var(--primary-color) 22%, transparent); }
  .key.faded { background: repeating-linear-gradient(135deg, var(--secondary-text-color) 0 1.5px, transparent 1.5px 4px); }
  .insights { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
  .insights li { display: grid; grid-template-columns: 20px 1fr; gap: 10px; font-size: 14px; line-height: 20px; }
  .insights ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
  .progress { display: block; height: 4px; margin-top: 6px; border-radius: 2px; background: var(--divider-color); overflow: hidden; }
  .progress i { display: block; height: 100%; background: var(--primary-color); }
  [role="tablist"] { display: flex; border-bottom: 1px solid var(--divider-color); margin: 2px -16px 0; padding: 0 8px; }
  [role="tab"] { min-height: 40px; padding: 0 12px; font-size: 14px; font-weight: 500; color: var(--secondary-text-color); border-bottom: 2px solid transparent; margin-bottom: -1px; }
  [role="tab"][aria-selected="true"] { color: var(--primary-color); border-bottom-color: var(--primary-color); }
  [role="tabpanel"] { display: flex; flex-direction: column; gap: 10px; }
  .muted { margin: 0; font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
  svg.chart { width: 100%; height: auto; display: block; overflow: visible; }
  .chart .grid { stroke: var(--divider-color); }
  .chart .tick { fill: var(--secondary-text-color); font-size: 12px; }
  .chart .rating { stroke: var(--secondary-text-color); stroke-dasharray: 4 4; }
  .chart .line { fill: none; stroke: var(--primary-color); stroke-width: 2.5; stroke-linejoin: round; }
  .chart .run { fill: color-mix(in srgb, var(--primary-color) 40%, transparent); }
  .chart .end { fill: var(--primary-color); }
  .chart .tag { font-size: 12px; fill: var(--secondary-text-color); }
  .chart .tag.strong { fill: var(--primary-text-color); font-weight: 600; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; list-style: none; padding: 0; margin: 4px 0 0; }
  .legend li { display: flex; align-items: center; gap: 6px; }
  .legend svg { width: 16px; height: 8px; flex: none; }
  .swatch-line { stroke: var(--primary-color); stroke-width: 2.5; }
  .swatch-run { fill: color-mix(in srgb, var(--primary-color) 40%, transparent); }
  .swatch-rating { stroke: var(--secondary-text-color); stroke-dasharray: 4 4; }
  .runs { list-style: none; margin: 0; padding: 0; }
  .runs li { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto 72px; gap: 12px; align-items: center; min-height: 44px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); font-size: 14px; line-height: 20px; }
  .runs li:last-child { border-bottom: 0; }
  .runs ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .runs .what { min-width: 0; }
  .runs .what span { display: block; margin-top: 2px; font-size: 12px; line-height: 16px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dev { display: flex; flex-direction: column; gap: 4px; }
  .dev span { position: relative; height: 6px; border-radius: 3px; background: var(--divider-color); }
  .dev span::before { content: ""; position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--secondary-text-color); }
  .dev i { position: absolute; top: 0; height: 6px; border-radius: 3px; background: var(--primary-color); }
  .dev b { font-size: 12px; line-height: 14px; font-weight: 400; text-align: center; color: var(--secondary-text-color); }
  .seg, .pager { display: flex; align-items: center; gap: 8px; }
  .seg button, .pager button { display: inline-flex; align-items: center; gap: 4px; min-height: 36px; padding: 0 12px; border-radius: 18px; font-size: 14px; border: 1px solid var(--divider-color); }
  .seg button[aria-pressed="true"] { color: var(--primary-color); border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
  .pager { justify-content: space-between; } .pager [aria-disabled="true"] { opacity: 0.4; cursor: default; }
  .pager ha-icon { --mdc-icon-size: 18px; }
  .meters { display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 9fr) auto; gap: 10px; align-items: center; font-size: 13px; }
  .meter { display: contents; }
  .meter .num { text-align: end; }
  .meter .rail { height: 8px; border-radius: 4px; background: var(--divider-color); overflow: hidden; }
  .meter .rail i { display: block; height: 100%; background: var(--primary-color); }
  .foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .link { display: inline-flex; align-items: center; gap: 4px; min-height: 32px; padding: 0 8px; margin-right: -8px; border-radius: 8px; font-size: 13px; color: var(--primary-color); }
  .link ha-icon { --mdc-icon-size: 18px; }
  .export { display: flex; justify-content: flex-end; }
  .stats { display: flex; flex-wrap: wrap; gap: 12px 28px; margin: 0; }
  .stats div { display: flex; flex-direction: column; }
  .stats dt { order: 2; font-size: 12px; color: var(--secondary-text-color); }
  .stats dd { margin: 0; font-size: 24px; line-height: 32px; font-weight: 500; }
  .fleet { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .fleet button { width: 100%; display: grid; grid-template-columns: minmax(96px, 1.2fr) minmax(0, 4fr) minmax(88px, auto);
    gap: 12px; align-items: center; text-align: left; padding: 8px; margin: 0 -8px; border-radius: 10px; }
  .fleet button:hover { background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
  .fleet .who { min-width: 0; }
  .fleet .who strong, .fleet .who span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fleet .who strong { font-weight: 500; font-size: 14px; }
  .fleet .who span, .fleet .tail span { font-size: 12px; color: var(--secondary-text-color); }
  .fleet .tail { text-align: right; font-size: 14px; }
  .fleet .tail > span { display: block; }
  .fleet .tail .band { margin-right: 0; }
  .fleet .tail .rte { white-space: nowrap; display: flex; justify-content: flex-end; align-items: center; gap: 3px; }
  .fleet .info { --mdc-icon-size: 14px; display: flex; color: var(--secondary-text-color); }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  .fleet .cell { height: 16px; border-radius: 4px; }
  .fleet .cell::after { display: none; }
  @media (min-width: 481px) {
    @supports (grid-template-columns: subgrid) {
      .fleet { display: grid; grid-template-columns: minmax(96px, 1.2fr) minmax(0, 4fr) minmax(88px, auto); column-gap: 12px; }
      .fleet li, .fleet button { grid-column: 1 / -1; display: grid; grid-template-columns: subgrid; }
    }
  }
  @media (max-width: 480px) {
    .lead { font-size: 18px; line-height: 26px; }
    .fleet button { grid-template-columns: minmax(0, 1fr) auto; }
    .fleet .bar { grid-column: 1 / -1; grid-row: 2; }
    .stats div { max-width: 10rem; }
  }
`;

// Without hass (the card picker, a bad config) the page language set by Home
// Assistant on <html lang> stands in.
function languageOf(hass) {
  const code = hass?.locale?.language || hass?.language || document.documentElement.lang || "en";
  return String(code).toLowerCase().startsWith("nl") ? "nl" : "en";
}

const copyFor = (hass) => COPY[languageOf(hass)];
const localeOf = (hass) => (languageOf(hass) === "nl" ? "nl-NL" : "en-GB");

function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function finite(value) {
  const number = typeof value === "number" ? value : Number(value);
  return value != null && value !== "" && Number.isFinite(number) ? number : null;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
// Two label heights moved apart around their midpoint until they are at least
// `gap` apart and inside `range`. A missing second label leaves the first alone.
function apart(first, second, gap, [top, bottom]) {
  if (second == null || Math.abs(first - second) >= gap) return [first, second];
  const middle = clamp((first + second) / 2, top + gap / 2, bottom - gap / 2);
  const half = first <= second ? -gap / 2 : gap / 2;
  return [middle + half, middle - half];
}

function fmt(hass, value, digits = 0) {
  if (value == null) return "—";
  return new Intl.NumberFormat(localeOf(hass), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

function kwh(hass, wh, digits = 2) {
  return wh == null ? "—" : `${fmt(hass, wh / 1000, digits)} kWh`;
}

function energy(hass, wh) {
  if (wh == null) return "—";
  return Math.abs(wh) < 1000 ? `${fmt(hass, wh)} Wh` : kwh(hass, wh);
}

const pct = (hass, value, digits = 0) => (value == null ? "—" : `${fmt(hass, value, digits)}%`);
const points = (hass, value) => `${fmt(hass, Math.abs(value), 1)} ${copyFor(hass).pointsWord}`;

function bigEnergy(hass, wh) {
  if (wh == null) return null;
  return wh >= 100000 ? `${fmt(hass, wh / 1000, 0)} kWh` : energy(hass, wh);
}

function duration(hass, hours) {
  const minutes = Math.round(hours * 60);
  if (minutes < 60) return `${minutes}\u00a0min`;
  if (minutes >= 48 * 60) return `${fmt(hass, minutes / 1440, 0)} ${copyFor(hass).daysWord}`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)}\u00a0h\u00a0${rest}\u00a0min` : `${minutes / 60}\u00a0h`;
}

function toDate(value) {
  if (value == null || value === "") return null;
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function day(hass, value, withTime = false) {
  const date = toDate(value);
  if (!date) return "—";
  const options = { day: "numeric", month: "short" };
  if (withTime) Object.assign(options, { hour: "2-digit", minute: "2-digit" });
  if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return new Intl.DateTimeFormat(localeOf(hass), options).format(date);
}

function stateOf(hass, entityId) {
  const state = entityId ? hass?.states?.[entityId] : null;
  return !state || state.state === "unknown" || state.state === "unavailable" ? null : state;
}

// Users can switch these sensors to kWh or kW; the cards work in Wh and W.
const UNIT_SCALE = { kWh: 1e3, MWh: 1e6, kW: 1e3, MW: 1e6 };

function numberOf(hass, entityId) {
  const state = stateOf(hass, entityId);
  const value = finite(state?.state);
  return value == null ? null : value * (UNIT_SCALE[state.attributes?.unit_of_measurement] ?? 1);
}

// Translation key of each entity the cards read. hass.entities carries no
// unique_id, so the translation key is the only stable match.
const KINDS = {
  capacity: "battery_usable_capacity",
  soh: "battery_state_of_health",
  soc: "battery_level",
  power: "battery_power",
  status: "battery_status",
  gridIn: "total_grid_input_energy",
  gridOut: "total_grid_output_energy",
  solar: "total_pv_energy",
  load: "total_load_energy",
  dod: "depth_of_discharge",
};
// DOD_DEFAULT_VALUE in pymarstek/const.py: the depth of discharge until one is written.
const DEFAULT_DOD = 88;

function kindOf(entity) {
  const key = entity?.translation_key;
  return Object.keys(KINDS).find((kind) => KINDS[kind] === key) || null;
}

function deviceName(hass, deviceId) {
  const device = hass?.devices?.[deviceId];
  return device?.name_by_user || device?.name || "Marstek";
}

// hass.entities and hass.devices are replaced, not mutated, when the
// registries change, so the scan only reruns after a registry update.
const packCache = new WeakMap();

function listPacks(hass) {
  const entities = hass?.entities;
  if (!entities) return [];
  const cached = packCache.get(entities);
  if (cached && cached.devices === hass.devices) return cached.packs;
  const packs = scanPacks(hass);
  packCache.set(entities, { devices: hass.devices, packs });
  return packs;
}

function scanPacks(hass) {
  const packs = new Map();
  for (const entity of Object.values(hass?.entities || {})) {
    if (entity.platform !== "marstek" || !entity.device_id || entity.disabled_by) continue;
    const kind = kindOf(entity);
    if (!kind) continue;
    const pack = packs.get(entity.device_id) || { deviceId: entity.device_id, entities: {} };
    pack.entities[kind] = entity.entity_id;
    packs.set(entity.device_id, pack);
  }
  return [...packs.values()]
    .filter((pack) => pack.entities.capacity)
    .sort((a, b) => deviceName(hass, a.deviceId).localeCompare(deviceName(hass, b.deviceId)));
}

function findPack(hass, config) {
  const packs = listPacks(hass);
  const owner = config?.entity ? hass?.entities?.[config.entity]?.device_id : null;
  return packs.find((pack) => pack.deviceId === config?.device_id) || packs.find((pack) => pack.deviceId === owner) || null;
}

function rteOf(raw) {
  const value = finite(raw);
  return value == null ? null : value <= 1.5 ? value * 100 : value;
}

function bandOf(health, confidence) {
  if (health == null || confidence === "collecting") return "measuring";
  // Round as the label does, so "80%" never sits next to "Worn".
  const shown = Math.round(health);
  if (shown >= GOOD_HEALTH) return "good";
  return shown >= WORN_HEALTH ? "fading" : "worn";
}

const BAND_ICON = { good: "mdi:heart-pulse", fading: "mdi:trending-down", worn: "mdi:alert-outline", measuring: "mdi:progress-clock" };
const FLOW_ICON = { charge: "mdi:battery-arrow-up", discharge: "mdi:battery-arrow-down", idle: "mdi:battery-outline" };

function flowOf(model) {
  if (model.status === "charging" || (model.power != null && model.power < -20)) return "charge";
  if (model.status === "discharging" || (model.power != null && model.power > 20)) return "discharge";
  return "idle";
}

function readModel(hass, pack) {
  const capacity = hass.states[pack.entities.capacity];
  const attrs = capacity?.attributes || {};
  const usable = numberOf(hass, pack.entities.capacity);
  const rated = finite(attrs.rated_capacity_wh);
  const reported = numberOf(hass, pack.entities.soc);
  const soc = reported == null ? null : clamp(reported, 0, 100);
  // A linked energy meter measures the round trip; the battery's own figure is implied.
  const metered = rteOf(attrs.plug_round_trip_efficiency);
  const model = {
    pack,
    name: deviceName(hass, pack.deviceId),
    attrs,
    usable,
    rated,
    health: numberOf(hass, pack.entities.soh) ?? finite(attrs.state_of_health),
    soc,
    power: numberOf(hass, pack.entities.power),
    plugPower: numberOf(hass, plugPowerId(hass, attrs)),
    dod: numberOf(hass, pack.entities.dod),
    status: stateOf(hass, pack.entities.status)?.state || null,
    stored: usable != null && soc != null ? (usable * soc) / 100 : null,
    rte: metered ?? rteOf(attrs.implied_round_trip_efficiency),
    rteMetered: metered != null,
    standby: finite(attrs.plug_standby_w),
    windows: Array.isArray(attrs.windows) ? attrs.windows : [],
    history: Array.isArray(attrs.history) ? attrs.history : [],
    active: attrs.active_window && typeof attrs.active_window === "object" ? attrs.active_window : null,
    confidence: attrs.confidence || "collecting",
    gridIn: numberOf(hass, pack.entities.gridIn),
    gridOut: numberOf(hass, pack.entities.gridOut),
    solar: numberOf(hass, pack.entities.solar),
    load: numberOf(hass, pack.entities.load),
  };
  model.flow = flowOf(model);
  model.band = bandOf(model.health, model.confidence);
  return model;
}

// --- shared pieces -------------------------------------------------------

function capacityCell(model, scaleWh) {
  const full = scaleWh || model.rated || model.usable || 1;
  const usable = model.usable ?? 0;
  const stored = clamp(model.stored ?? 0, 0, usable);
  const faded = model.rated != null ? Math.max(0, model.rated - usable) : 0;
  const width = (wh) => `${clamp((wh / full) * 100, 0, 100).toFixed(2)}%`;
  return `<span class="seg stored" style="width:${width(stored)}"></span>`
    + `<span class="seg room" style="width:${width(usable - stored)}"></span>`
    + `<span class="seg faded" style="width:${width(faded)}"></span>`;
}

function flowHtml(hass, model, text) {
  const watts = model.power == null || model.flow === "idle" ? "" : ` <span class="num">${fmt(hass, Math.abs(model.power))} W</span>`;
  const plug = model.plugPower == null ? "" : ` · <span class="num">${esc(text.atPlug(`${fmt(hass, Math.abs(model.plugPower))} W`))}</span>`;
  return `<span class="flow"><ha-icon icon="${FLOW_ICON[model.flow]}"></ha-icon>${esc(text.flow[model.flow])}${watts}${plug}</span>`;
}

// --- health card ---------------------------------------------------------

function headlineHtml(hass, model, text) {
  const band = `<span class="band ${model.band}"><ha-icon icon="${BAND_ICON[model.band]}"></ha-icon>${esc(text.band[model.band])}</span>`;
  if (model.usable == null) return `<p class="lead">${band}${esc(text.headlineWait)}</p>`;
  const usable = `<strong class="num">${esc(kwh(hass, model.usable))}</strong>`;
  // A reading just over the rating is estimate noise, so it is not shown as 101%.
  const words = model.rated == null || model.health == null ? text.headlineNoRating("§")
    : Math.round(model.health) >= 100 ? text.headlineFull("§", kwh(hass, model.rated))
      : text.headline("§", pct(hass, model.health), kwh(hass, model.rated));
  const sentence = esc(words).replace("§", usable);
  return `<p class="lead">${band}${sentence}</p>`;
}

function capacityHtml(hass, model, text) {
  if (model.usable == null) return "";
  const faded = model.rated != null ? Math.max(0, model.rated - model.usable) : null;
  const label = text.barLabel(energy(hass, model.stored), kwh(hass, model.usable), kwh(hass, model.rated));
  const keys = [
    model.stored == null ? "" : `<li><i class="key stored"></i>${esc(text.stored(energy(hass, model.stored)))}</li>`,
    model.stored == null ? "" : `<li><i class="key room"></i>${esc(text.usableEmpty(energy(hass, model.usable - model.stored)))}</li>`,
    faded ? `<li><i class="key faded"></i>${esc(text.faded(energy(hass, faded)))}</li>` : "",
  ].join("");
  return `<div class="cap"><div class="cell" role="img" aria-label="${esc(label)}">${capacityCell(model)}</div><ul class="keys num">${keys}</ul></div>`;
}

function trendInsight(hass, model, text) {
  const weeks = model.history.filter((point) => finite(point.usable_wh) != null && finite(point.week_start) != null);
  if (weeks.length < 2 || model.rated == null) return null;
  const first = weeks[0];
  const last = weeks[weeks.length - 1];
  const days = (last.week_start - first.week_start) / DAY_S;
  if (days < MIN_TREND_DAYS) return null;
  const delta = last.usable_wh - first.usable_wh;
  const spread = finite(model.attrs.spread_wh) ?? 0;
  const span = duration(hass, days * 24);
  if (Math.abs(delta) <= spread / 2) return { icon: "mdi:chart-line-variant", text: text.steady(span) };
  const deltaPoints = (delta / model.rated) * 100;
  const sign = delta < 0 ? "−" : "+";
  return {
    icon: delta < 0 ? "mdi:trending-down" : "mdi:trending-up",
    text: text.trend(`${sign}${energy(hass, Math.abs(delta))}`, `${sign}${points(hass, deltaPoints)}`, points(hass, deltaPoints / (days / 30.44))),
  };
}

function runtimeInsight(hass, model, text) {
  const watts = model.power == null ? 0 : Math.abs(model.power);
  if (watts < 50 || model.usable == null || model.soc == null) return null;
  const power = `${fmt(hass, watts)} W`;
  // The pack stops at 100 minus the depth of discharge, never at 0%. The Open
  // API cannot read that setting back, so the number is the last write, or the
  // firmware default without the entity, and the sentence names the floor.
  if (model.flow === "discharge") {
    const floor = 100 - (model.dod ?? DEFAULT_DOD);
    const left = (model.usable * (model.soc - floor)) / 100;
    return left > 0 ? { icon: "mdi:timer-sand", text: text.empties(power, pct(hass, floor), duration(hass, left / watts)) } : null;
  }
  if (model.flow === "charge" && model.soc < 100) {
    const room = model.usable - (model.stored ?? 0);
    return { icon: "mdi:timer-sand", text: text.fills(power, duration(hass, room / watts)) };
  }
  return null;
}

function insightsHtml(hass, model, text) {
  const runs = model.windows.length;
  const items = [];
  if (runs) {
    const since = day(hass, Math.min(...model.windows.map((item) => finite(item.closed_at) ?? Infinity)));
    const spread = finite(model.attrs.spread_wh);
    const half = spread != null && model.usable ? pct(hass, (spread / 2 / model.usable) * 100, 1) : "";
    items.push({ icon: "mdi:counter", text: text.agree(runs, since, half) });
  }
  items.push(trendInsight(hass, model, text));
  if (model.rte != null) {
    const args = [pct(hass, model.rte, 1), `${fmt(hass, model.rte / 10, 1)} kWh`];
    const rte = model.rteMetered ? text.rteMetered(...args, day(hass, model.attrs.plug_round_trip_since)) : text.rte(...args);
    items.push({ icon: "mdi:swap-vertical", text: rte });
  }
  if (model.standby != null) {
    items.push({ icon: "mdi:power-sleep", text: text.standby(`${fmt(hass, model.standby, 1)} W`, kwh(hass, model.standby * 24)) });
  }
  items.push(runtimeInsight(hass, model, text));
  const active = model.active;
  if (active && (finite(active.soc_delta) ?? 0) > 0) {
    const target = finite(active.target_soc_delta) || 20;
    const done = clamp(finite(active.soc_delta), 0, target);
    const label = text.measuring(text.runWord[active.direction] || "", fmt(hass, done, 0), fmt(hass, target));
    const bar = `<span class="progress" role="progressbar" aria-label="${esc(label)}"
      aria-valuemin="0" aria-valuemax="${target}" aria-valuenow="${done}"><i style="width:${((done / target) * 100).toFixed(1)}%"></i></span>`;
    items.push({ icon: "mdi:progress-clock", text: label, extra: bar });
  }
  const rows = items.filter(Boolean).map((item) => `<li><ha-icon icon="${item.icon}"></ha-icon><span>${esc(item.text)}${item.extra || ""}</span></li>`);
  return rows.length ? `<ul class="insights num">${rows.join("")}</ul>` : "";
}

// Drawn at the card's own width in CSS pixels, so the labels keep their size on
// a phone and on a wide screen instead of scaling with the card.
function capacityChart(hass, model, text, width = 360) {
  const weeks = model.history
    .filter((point) => finite(point.usable_wh) != null && finite(point.week_start) != null)
    .map((point) => ({ t: point.week_start, wh: point.usable_wh }));
  if (!weeks.length) return `<p class="muted">${esc(text.noWeeks)}</p>`;
  const runs = model.windows.map((item) => ({ t: finite(item.closed_at), wh: finite(item.capacity_wh) })).filter((item) => item.t != null && item.wh != null);
  const values = [...weeks, ...runs].map((item) => item.wh).concat(model.rated ?? []);
  const low = Math.floor((Math.min(...values) * 0.98) / 100) * 100;
  const high = Math.ceil((Math.max(...values) * 1.01) / 100) * 100;
  const times = [...weeks, ...runs].map((item) => item.t);
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const W = clamp(width - 32, 260, 760);
  const [H, L, R, T, B] = [Math.round(clamp(W * 0.4, 150, 220)), 40, 76, 10, 22];
  const x = (t) => L + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - L - R);
  const y = (wh) => T + ((high - wh) / (high - low || 1)) * (H - T - B);
  const ticks = [low, (low + high) / 2, high].map((wh) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(wh).toFixed(1)}" y2="${y(wh).toFixed(1)}"/>
    <text class="tick num" x="${L - 6}" y="${(y(wh) + 4).toFixed(1)}" text-anchor="end">${fmt(hass, wh / 1000, 2)}</text>`).join("");
  const dates = [t0, t1].map((t, index) => `<text class="tick" x="${x(t).toFixed(1)}" y="${H - 4}" text-anchor="${index ? "end" : "start"}">${esc(day(hass, t))}</text>`).join("");
  const dots = runs.map((item) => `<circle class="run" cx="${x(item.t).toFixed(1)}" cy="${y(item.wh).toFixed(1)}" r="2.5"/>`).join("");
  const last = weeks[weeks.length - 1];
  // The newest estimate holds until the next week's, so the line runs on to the
  // newest run and ends beside its label, even when there is one week so far.
  const path = [...weeks, { t: t1, wh: last.wh }].map((item, index) => `${index ? "L" : "M"}${x(item.t).toFixed(1)} ${y(item.wh).toFixed(1)}`).join(" ");
  const [endY, ratingY] = apart(y(last.wh), model.rated == null ? null : y(model.rated), 14, [T + 4, H - B]);
  const rating = model.rated == null ? "" : `<line class="rating" x1="${L}" x2="${W - R}" y1="${y(model.rated).toFixed(1)}" y2="${y(model.rated).toFixed(1)}"/>
    <text class="tag" x="${W - R + 6}" y="${(ratingY + 4).toFixed(1)}">${esc(text.rating)}</text>`;
  const summary = text.capacityChart(kwh(hass, weeks[0].wh), kwh(hass, last.wh));
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(summary)}"><title>${esc(summary)}</title>
    ${ticks}${rating}${dots}<path class="line" d="${path}"/>
    <circle class="end" cx="${x(t1).toFixed(1)}" cy="${y(last.wh).toFixed(1)}" r="4"/>
    <text class="tag strong num" x="${W - R + 6}" y="${(endY + 4).toFixed(1)}">${esc(kwh(hass, last.wh))}</text>
    ${dates}</svg>
    <ul class="legend muted" aria-hidden="true">
      <li><svg viewBox="0 0 16 8"><line class="swatch-line" x1="0" x2="16" y1="4" y2="4"/></svg>${esc(text.weekly)}</li>
      <li><svg viewBox="0 0 16 8"><circle class="swatch-run" cx="8" cy="4" r="2.5"/></svg>${esc(text.runDots)}</li>
      ${model.rated == null ? "" : `<li><svg viewBox="0 0 16 8"><line class="swatch-rating" x1="0" x2="16" y1="4" y2="4"/></svg>${esc(text.rating)}</li>`}
    </ul>`;
}

const RUNS_PER_PAGE = 8;

// Rounded first, so a run 0.04 % under the median reads 0,0 % and not −0,0 %.
function signedPct(hass, value) {
  const rounded = Math.round(value * 10) / 10 || 0;
  return `${rounded > 0 ? "+" : rounded < 0 ? "\u2212" : ""}${fmt(hass, Math.abs(rounded), 1)}%`;
}

function runRowHtml(hass, item, base, text) {
  const wh = finite(item.capacity_wh);
  const off = base && wh != null ? ((wh - base) / base) * 100 : null;
  const reach = off == null ? 0 : clamp(Math.abs(off) / 3, 0, 1) * 50;
  const dev = off == null ? "<span></span>" : `<span class="dev" role="img" aria-label="${esc(`${signedPct(hass, off)} ${text.vsMedian}`)}"
    ><span><i style="left:${off < 0 ? 50 - reach : 50}%;width:${reach.toFixed(1)}%"></i></span><b class="num" aria-hidden="true">${signedPct(hass, off)}</b></span>`;
  // Runs saved by older versions keep only the size of the move, not its ends.
  const [from, to] = [finite(item.start_soc), finite(item.end_soc)];
  const what = from != null && to != null ? text.runRow(item.direction, pct(hass, from), pct(hass, to))
    : text.runSpan(item.direction, `${fmt(hass, finite(item.soc_delta) ?? 0)} ${text.pointsWord}`);
  const solar = finite(item.pv_share);
  const detail = [day(hass, item.closed_at, true), solar ? text.solar(pct(hass, solar * 100)) : ""].filter(Boolean).join(" · ");
  return `<li><ha-icon icon="${item.direction === "charge" ? "mdi:arrow-up" : "mdi:arrow-down"}"></ha-icon>
    <span class="what">${esc(what)}<span class="num">${esc(detail)}</span></span>
    <strong class="num">${esc(kwh(hass, wh))}</strong>${dev}</li>`;
}

// Newest first, by direction, a page at a time. data-action lets a redraw
// put focus back on the button that was pressed.
function runsHtml(hass, model, text, { filter, page }) {
  if (!model.windows.length) return `<p class="muted">${esc(text.noRuns)}</p>`;
  const median = (direction) => finite(model.attrs[`${direction}_median_wh`]);
  const both = new Set(model.windows.map((item) => item.direction)).size > 1;
  const runs = [...model.windows].reverse().filter((item) => !both || filter === "all" || item.direction === filter);
  const pages = Math.ceil(runs.length / RUNS_PER_PAGE);
  const first = clamp(page, 0, pages - 1) * RUNS_PER_PAGE;
  const rows = runs.slice(first, first + RUNS_PER_PAGE).map((item) => runRowHtml(hass, item, median(item.direction), text));
  const seg = !both ? "" : `<div class="seg" role="group" aria-label="${esc(text.tabs.runs)}">${Object.entries(text.runFilter).map(([name, label]) =>
    `<button type="button" data-action="runs-${name}" aria-pressed="${name === filter}">${esc(label)}</button>`).join("")}</div>`;
  // The redraw focuses a new button, so readers announce its name and the range.
  const step = (action, icon, label, off) => `<button type="button" data-action="${action}" aria-disabled="${off}"
    aria-describedby="runs-range"><ha-icon icon="${icon}"></ha-icon>${esc(label)}</button>`;
  const pager = pages < 2 ? "" : `<nav class="pager" aria-label="${esc(text.runPages.label)}" data-page="${first / RUNS_PER_PAGE}">${step("page-prev", "mdi:chevron-left", text.runPages.prev, first === 0)}
    <span class="muted num" id="runs-range">${esc(text.range(first + 1, first + rows.length, runs.length))}</span>${step("page-next", "mdi:chevron-right", text.runPages.next, first + RUNS_PER_PAGE >= runs.length)}</nav>`;
  return `<p class="muted num">${esc(text.medians(kwh(hass, median("discharge")), kwh(hass, median("charge"))))}</p>${seg}<ul class="runs">${rows.join("")}</ul>${pager}`;
}

function energyHtml(hass, model, text) {
  const rows = [[text.charged, model.gridIn], [text.dischargedAc, model.gridOut], [text.solarIn, model.solar], [text.homeLoad, model.load]];
  const top = Math.max(1, ...rows.map(([, wh]) => wh ?? 0));
  const meters = rows.map(([label, wh]) => `<div class="meter"><span>${esc(label)}</span>
    <span class="rail"><i style="width:${(((wh ?? 0) / top) * 100).toFixed(1)}%"></i></span>
    <span class="num">${esc(bigEnergy(hass, wh) ?? text.notReported)}</span></div>`).join("");
  const cycles = model.gridOut != null && model.rated ? model.gridOut / model.rated : null;
  const line = cycles == null ? "" : cycles < 1 ? text.cyclesNone : text.cycles(fmt(hass, cycles, cycles < 10 ? 1 : 0));
  return `<div class="meters">${meters}</div>${line ? `<p class="muted">${esc(line)}</p>` : ""}`;
}

function tabsHtml(hass, model, text, selected, cardId, width, runs) {
  const tabs = TABS.map((name) => `<button type="button" role="tab" id="${cardId}-${name}" aria-controls="${cardId}-panel"
    aria-selected="${name === selected}" tabindex="${name === selected ? 0 : -1}" data-tab="${name}">${esc(text.tabs[name])}</button>`).join("");
  const panel = selected === "runs" ? runsHtml(hass, model, text, runs) : selected === "energy" ? energyHtml(hass, model, text) : capacityChart(hass, model, text, width);
  return `<div role="tablist" aria-label="${esc(text.tabList)}">${tabs}</div>
    <div role="tabpanel" id="${cardId}-panel" aria-labelledby="${cardId}-${selected}" tabindex="0">${panel}${exportHtml(model, selected, text)}</div>`;
}

function healthCardHtml(hass, config, tab, cardId, width, runs) {
  const text = copyFor(hass);
  const pack = hass ? findPack(hass, config) : null;
  if (!pack) return `<style>${STYLES}</style><ha-card><p class="muted">${esc(text.pickDevice)}</p></ha-card>`;
  const model = readModel(hass, pack);
  const tabs = config.show_tabs === false ? "" : tabsHtml(hass, model, text, tab, cardId, width, runs);
  return `<style>${STYLES}</style><ha-card>
    <div class="top"><ha-icon icon="mdi:battery-heart-variant"></ha-icon><h2 class="name">${esc(config.title || model.name)}</h2>${flowHtml(hass, model, text)}</div>
    ${headlineHtml(hass, model, text)}
    ${capacityHtml(hass, model, text)}
    ${insightsHtml(hass, model, text)}
    ${tabs}
    <div class="foot"><p class="muted">${esc(text.source(text.confidence[model.confidence] || model.confidence))}</p>
      <button class="link" type="button" data-action="device"><ha-icon icon="mdi:cog-outline"></ha-icon>${esc(text.device)}</button></div>
  </ha-card>`;
}

// --- fleet card ----------------------------------------------------------

function sum(values) {
  const known = values.filter((value) => value != null);
  return known.length ? known.reduce((total, value) => total + value, 0) : null;
}

function fleetInsights(hass, models, text) {
  const out = [];
  const usable = sum(models.map((model) => model.usable));
  const measured = models.filter((model) => model.usable != null);
  if (measured.length > 1 && usable) {
    const biggest = measured.reduce((a, b) => (b.usable > a.usable ? b : a));
    out.push({ icon: "mdi:chart-pie-outline", text: text.fleetShare(biggest.name, pct(hass, (biggest.usable / usable) * 100)) });
  }
  const healthy = models.filter((model) => model.health != null);
  // Name the most faded pack only when it has left the Healthy band.
  const weakest = healthy.length > 1 ? healthy.reduce((a, b) => (b.health < a.health ? b : a)) : null;
  if (weakest && weakest.band !== "good") {
    out.push({ icon: BAND_ICON[weakest.band], text: text.fleetWeakest(weakest.name, pct(hass, weakest.health, 1)) });
  }
  // Compare like with like: metered round trips include standby, implied ones do not.
  const rated = models.filter((model) => model.rte != null);
  const metered = rated.filter((model) => model.rteMetered);
  const efficient = metered.length > 1 ? metered : rated.filter((model) => !model.rteMetered);
  if (efficient.length > 1) {
    const best = Math.max(...efficient.map((model) => model.rte));
    const worst = efficient.reduce((a, b) => (b.rte < a.rte ? b : a));
    if (best - worst.rte >= 1) out.push({ icon: "mdi:swap-vertical", text: text.fleetRte(worst.name, points(hass, best - worst.rte)) });
  }
  const charged = models.filter((model) => model.soc != null);
  if (charged.length > 1) {
    const lowest = charged.reduce((a, b) => (b.soc < a.soc ? b : a));
    const highest = charged.reduce((a, b) => (b.soc > a.soc ? b : a));
    if (highest.soc - lowest.soc >= 10) out.push({ icon: "mdi:scale-unbalanced", text: text.fleetSoc(points(hass, highest.soc - lowest.soc), lowest.name, highest.name) });
  }
  return out;
}

function fleetRow(hass, model, text, scale) {
  const health = model.health == null ? text.band.measuring : text.health(pct(hass, model.health));
  const label = `${model.name}: ${text.barLabel(energy(hass, model.stored), kwh(hass, model.usable), kwh(hass, model.rated))}`;
  const watts = model.power == null || model.flow === "idle" ? "" : `${fmt(hass, Math.abs(model.power))} W`;
  const status = [text.flow[model.flow], watts, model.soc == null ? "" : pct(hass, model.soc)].filter(Boolean).join(" · ");
  return `<li><button type="button" data-entity="${esc(model.pack.entities.capacity)}">
    <span class="who"><strong>${esc(model.name)}</strong><span class="num">${esc(status)}</span></span>
    <span class="bar"><span class="cell" role="img" aria-label="${esc(label)}">${capacityCell(model, scale)}</span></span>
    <span class="tail num"><span class="band ${model.band}"><ha-icon icon="${BAND_ICON[model.band]}"></ha-icon>${esc(health)}</span
      ><span class="rte">${esc(kwh(hass, model.usable))}${rteTail(hass, model, text)}</span></span>
  </button></li>`;
}

// How the round trip was found sits behind an info icon: hover shows it, a screen reader reads it.
function rteTail(hass, model, text) {
  if (model.rte == null) return "";
  const hint = model.rteMetered ? text.rteHint.metered : text.rteHint.implied;
  return ` · ${esc(text.rteShort(pct(hass, model.rte)))}<ha-icon class="info" icon="mdi:information-outline" title="${esc(hint)}"></ha-icon><span class="sr">, ${esc(hint)}</span>`;
}

// Shown only when at least one pack has a linked meter with a power sensor.
function plugStat(hass, models, text) {
  const metered = models.filter((model) => model.plugPower != null);
  if (!metered.length) return null;
  const watts = sum(metered.map((model) => model.plugPower));
  // A partial sum sits beside the whole fleet's battery power, so say whose plug it is.
  const partial = metered.length === 1 ? metered[0].name : text.ofTotal(metered.length, models.length);
  const label = text.fleetPlug(metered.length < models.length ? partial : "");
  return [`${fmt(hass, Math.abs(watts))} W`, `${label} · ${text.plugFlow[plugFlow(watts)]}`];
}

function fleetCardHtml(hass) {
  const text = copyFor(hass);
  const models = listPacks(hass).map((pack) => readModel(hass, pack));
  if (!models.length) return `<style>${STYLES}</style><ha-card><p class="muted">${esc(text.fleetEmpty)}</p></ha-card>`;
  const usable = sum(models.map((model) => model.usable));
  const stored = sum(models.map((model) => model.stored));
  const power = sum(models.map((model) => model.power));
  const flow = power == null ? "idle" : power > 20 ? "discharge" : power < -20 ? "charge" : "idle";
  const scale = Math.max(...models.map((model) => model.rated ?? model.usable ?? 0)) || 1;
  const stats = [
    [kwh(hass, usable), text.fleetUsable],
    [kwh(hass, stored), text.fleetStored(usable && stored != null ? pct(hass, (stored / usable) * 100) : "—")],
    [power == null ? "—" : `${fmt(hass, Math.abs(power))} W`, `${text.fleetPower} · ${text.flow[flow].toLowerCase()}`],
    plugStat(hass, models, text),
  ].filter(Boolean).map(([value, label]) => `<div><dt>${esc(label)}</dt><dd class="num">${esc(value)}</dd></div>`).join("");
  const insights = fleetInsights(hass, models, text).map((item) => `<li><ha-icon icon="${item.icon}"></ha-icon><span>${esc(item.text)}</span></li>`).join("");
  return `<style>${STYLES}</style><ha-card>
    <div class="top"><ha-icon icon="mdi:home-battery-outline"></ha-icon><h2 class="name">${esc(text.fleetTitle)}</h2></div>
    <dl class="stats">${stats}</dl>
    <ul class="fleet">${models.map((model) => fleetRow(hass, model, text, scale)).join("")}</ul>
    ${models.length > 1 ? `<p class="muted">${esc(text.fleetScale)}</p>` : ""}
    ${insights ? `<ul class="insights num">${insights}</ul>` : ""}
  </ha-card>`;
}

// --- elements ------------------------------------------------------------

function fire(host, type, detail) {
  host.dispatchEvent(new CustomEvent(type, { bubbles: true, composed: true, detail }));
}

function openMoreInfo(host, entityId) {
  if (entityId) fire(host, "hass-more-info", { entityId });
}

// --- CSV export ----------------------------------------------------------

// Columns keep the attribute names. Every cell is a number, a UTC time or a
// fixed word, so no cell can start a spreadsheet formula and none needs quotes.
// Keyed by the tab that offers the download.
const CSV_COLUMNS = {
  capacity: ["week_start", "usable_wh", "rated_wh"],
  runs: ["closed_at", "direction", "start_soc", "end_soc", "soc_delta", "capacity_wh", "duration_s", "average_power_w", "pv_share"],
};

function csvCell(row, column) {
  if (column === "closed_at" || column === "week_start") return toDate(row[column])?.toISOString() ?? "";
  if (column === "direction") return row.direction === "charge" ? "charge" : "discharge";
  return finite(row[column]) ?? "";
}

const csvRows = (model, tab) => (tab === "runs" ? model.windows : model.history.map((point) => ({ ...point, rated_wh: model.rated })));

function csvOf(model, tab) {
  const columns = CSV_COLUMNS[tab];
  return [columns, ...csvRows(model, tab).map((row) => columns.map((column) => csvCell(row, column)))].map((cells) => `${cells.join(",")}\r\n`).join("");
}

function exportHtml(model, tab, text) {
  if (!CSV_COLUMNS[tab] || !csvRows(model, tab).length) return "";
  return `<div class="export"><button class="link" type="button" data-action="csv-${tab}">
    <ha-icon icon="mdi:download"></ha-icon>${esc(text.csv[tab])}</button></div>`;
}

// The same steps as Home Assistant's own history download. The Android app
// reads the blob after the click, so it is revoked ten seconds later.
function saveCsv(model, tab) {
  const url = URL.createObjectURL(new Blob([csvOf(model, tab)], { type: "text/csv" }));
  const slug = model.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "marstek";
  const link = Object.assign(document.createElement("a"), { href: url, download: `${slug}-${tab === "runs" ? "runs" : "weekly"}.csv`, target: "_blank" });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function openDevice(host, deviceId) {
  if (!deviceId) return;
  history.pushState(null, "", `/config/devices/device/${deviceId}`);
  fire(host, "location-changed", { replace: false });
}

// Only the packs a card shows decide whether it redraws. last_updated moves
// on any state or attribute change, so a new rating or spread redraws too.
function signatureOf(hass, packs, extra) {
  return JSON.stringify([extra, languageOf(hass), packs.map((pack) => [
    deviceName(hass, pack.deviceId),
    Object.values(pack.entities).map((id) => hass.states[id]?.last_updated),
    hass.states[plugPowerId(hass, hass.states[pack.entities.capacity]?.attributes)]?.last_updated,
  ])]);
}

// Selector for the focused control, so a redraw can put focus back on the
// same tab, button, row or panel.
function focusKey(root) {
  const active = root.activeElement;
  if (!active) return null;
  for (const name of ["tab", "action", "entity"]) {
    const value = active.dataset?.[name];
    if (value) return `[data-${name}="${CSS.escape(value)}"]`;
  }
  return active.getAttribute("role") === "tabpanel" ? '[role="tabpanel"]' : null;
}

// Home Assistant's scoped-registry polyfill wraps innerHTML, and the new
// nodes cannot take focus until a microtask later.
function redraw(root, html, key = focusKey(root)) {
  root.innerHTML = html;
  if (key) queueMicrotask(() => root.querySelector(key)?.focus());
}

// Shared by both cards: a shadow root, full-width sizing, and a redraw that
// runs only when what the card shows has changed.
class MarstekCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  set hass(hass) {
    this._hass = hass;
    this._render(false);
  }

  setConfig(config) {
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      throw new Error(copyFor(this._hass).invalidConfig);
    }
    this._config = { ...config };
    this._render(true);
  }

  getGridOptions() { return { columns: "full", min_columns: 6, rows: "auto" }; }

  _render(force, focusTarget) {
    if (!this._hass) return;
    const signature = signatureOf(this._hass, this._packs(), this._view());
    if (!force && signature === this._signature) return;
    this._signature = signature;
    redraw(this.shadowRoot, this._html(), focusTarget);
  }
}

let cardCount = 0;

class MarstekBatteryHealthCard extends MarstekCard {
  constructor() {
    super();
    this._tab = TABS[0];
    this._runs = { filter: "all", page: 0 };
    this._id = `mbh${(cardCount += 1)}`;
    this.shadowRoot.addEventListener("click", (event) => this._onClick(event));
    this.shadowRoot.addEventListener("keydown", (event) => this._onKey(event));
  }

  static getConfigElement() { return document.createElement(EDITOR); }

  static getStubConfig(hass) {
    const pack = listPacks(hass)[0];
    return pack ? { device_id: pack.deviceId } : {};
  }

  setConfig(config) {
    this._tab = TABS.includes(config?.default_tab) ? config.default_tab : TABS[0];
    super.setConfig(config);
  }

  getCardSize() { return this._config.show_tabs === false ? 5 : 8; }

  _packs() {
    const pack = findPack(this._hass, this._config);
    return pack ? [pack] : [];
  }

  _view() { return [this._tab, this._config, this._width, this._runs]; }

  _html() { return healthCardHtml(this._hass, this._config, this._tab, this._id, this._width, this._runs); }

  // Redraw when the width moves to another 40 px step, so the chart fits it.
  connectedCallback() {
    this._resize ||= new ResizeObserver(([entry]) => {
      this._width = Math.round(entry.contentRect.width / 40) * 40 || undefined;
      this._render(false);
    });
    this._resize.observe(this);
  }

  disconnectedCallback() { this._resize?.disconnect(); }

  _select(tab) {
    this._tab = tab;
    this._render(true, `[data-tab="${tab}"]`);
  }

  _onClick(event) {
    const tab = event.target.closest("[data-tab]");
    if (tab) {
      this._select(tab.dataset.tab);
      return;
    }
    const action = event.target.closest("[data-action]");
    if (!action || action.getAttribute("aria-disabled") === "true") return;
    const [verb, arg] = action.dataset.action.split("-");
    const pack = findPack(this._hass, this._config);
    if (verb === "device") openDevice(this, pack?.deviceId);
    else if (verb === "csv" && pack) saveCsv(readModel(this._hass, pack), arg);
    else if (verb === "runs" || verb === "page") this._showRuns(verb, arg, action);
  }

  // A filter starts again on page one. A page step counts from the page on
  // screen, because the stored one can be past the end once runs drop out.
  _showRuns(verb, arg, button) {
    const shown = Number(button.closest("[data-page]")?.dataset.page ?? 0);
    this._runs = verb === "runs" ? { filter: arg, page: 0 } : { ...this._runs, page: shown + (arg === "next" ? 1 : -1) };
    this._render(true, `[data-action="${verb}-${arg}"]`);
  }

  _onKey(event) {
    const tab = event.target.closest("[data-tab]");
    if (!tab) return;
    const index = TABS.indexOf(tab.dataset.tab);
    const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: TABS.length - 1 }[event.key];
    if (next == null) return;
    event.preventDefault();
    this._select(TABS[(next + TABS.length) % TABS.length]);
  }
}

class MarstekBatteryFleetCard extends MarstekCard {
  constructor() {
    super();
    this.shadowRoot.addEventListener("click", (event) => {
      openMoreInfo(this, event.target.closest("[data-entity]")?.dataset.entity);
    });
  }

  static getStubConfig() { return {}; }

  getCardSize() { return 4; }

  _packs() { return listPacks(this._hass); }

  _view() { return "fleet"; }

  _html() { return fleetCardHtml(this._hass); }
}

class MarstekBatteryHealthCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._draw();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._draw();
  }

  _draw() {
    if (!this._hass || !this._config) return;
    const text = copyFor(this._hass);
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (field) => ({ device_id: text.editorDevice, title: text.editorTitle, default_tab: text.editorTab, show_tabs: text.editorTabs })[field.name];
      this._form.addEventListener("value-changed", (event) => {
        const next = { ...event.detail.value };
        for (const key of Object.keys(next)) if (next[key] === "" || next[key] == null) delete next[key];
        this._config = next;
        fire(this, "config-changed", { config: next });
      });
      this.append(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "device_id", selector: { device: { integration: "marstek" } } },
      { name: "title", selector: { text: {} } },
      { name: "show_tabs", selector: { boolean: {} } },
      { name: "default_tab", selector: { select: { mode: "dropdown", options: TABS.map((tab) => ({ value: tab, label: text.tabs[tab] })) } } },
    ];
    this._form.data = { show_tabs: true, default_tab: TABS[0], ...this._config };
  }
}

class MarstekBatteryHealthStrategy extends HTMLElement {
  static registryDependencies = ["devices", "entities"];

  static async generate(_config, hass) {
    const packs = listPacks(hass);
    return {
      title: copyFor(hass).dashboardTitle,
      views: [{
        path: "health",
        type: "sections",
        max_columns: 2,
        sections: [
          { type: "grid", column_span: 2, cards: [{ type: `custom:${FLEET_CARD}` }] },
          ...packs.map((pack) => ({ type: "grid", cards: [{ type: `custom:${HEALTH_CARD}`, device_id: pack.deviceId }] })),
        ],
      }],
    };
  }
}

function announce() {
  const text = copyFor(null);
  window.customCards = window.customCards || [];
  for (const card of [
    { type: HEALTH_CARD, name: text.cardName, description: text.cardDescription },
    { type: FLEET_CARD, name: text.fleetName, description: text.fleetDescription },
  ]) {
    if (!window.customCards.some((known) => known.type === card.type)) window.customCards.push({ ...card, preview: true, documentationURL: DOCS });
  }
  window.customStrategies = window.customStrategies || [];
  if (!window.customStrategies.some((known) => known.type === STRATEGY)) {
    window.customStrategies.push({ type: STRATEGY, strategyType: "dashboard", name: text.strategyName, description: text.strategyDescription, documentationURL: DOCS });
  }
}

function register() {
  const elements = [
    [HEALTH_CARD, MarstekBatteryHealthCard],
    [FLEET_CARD, MarstekBatteryFleetCard],
    [EDITOR, MarstekBatteryHealthCardEditor],
    [`ll-strategy-dashboard-${STRATEGY}`, MarstekBatteryHealthStrategy],
  ];
  for (const [name, element] of elements) if (!window.customElements.get(name)) window.customElements.define(name, element);
  announce();
}

// Home Assistant replaces window.customElements with a scoped registry when
// <home-assistant> is defined. Anything defined before that lands in the old
// registry, so the strategy would never be found on a cold dashboard load.
if (window.customElements.get("home-assistant")) register();
else window.customElements.whenDefined("home-assistant").then(register);
