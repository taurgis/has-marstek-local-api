// Marstek capacity dossier. Loaded by the integration; no Lovelace resource.
// Sections grid: contributors.home-assistant.io/frontend/custom-ui/card/
// Strategies: contributors.home-assistant.io/frontend/custom-ui/strategy/

const CARD_TYPE = "marstek-capacity-card";
const OVERVIEW_TYPE = "marstek-capacity-overview-card";
const STRATEGY_TYPE = "marstek-capacity";
const WARRANTY_FLOOR = 75;
const CYCLE_REFERENCE = 6000;
const TABS = ["overview", "trend", "lifetime"];

const COPY = {
  en: {
    dashboardTitle: "Battery Capacity",
    overviewTitle: "Marstek batteries",
    overviewEmpty: "No Marstek battery with a usable-capacity sensor is set up yet.",
    pickDevice: "Choose a Marstek device in the card editor.",
    nominal: "nominal",
    healthy: "Healthy",
    watch: "Watch",
    weak: "Weak",
    building: (count) => `Building (${Math.min(count, 3)}/3 windows)`,
    confidence: {
      low: "Low confidence",
      medium: "Medium confidence",
      high: "High confidence",
    },
    windows: (count) => `${count} window${count === 1 ? "" : "s"}`,
    settings: "Device settings",
    soh: "State of health",
    warranty: "75% warranty floor",
    spec: "Cycle spec",
    belowRated: "below nominal",
    aboveRated: "above nominal",
    atRated: "at nominal",
    discharge: "Discharge median",
    charge: "Charge median",
    noWindow: "No window yet",
    pvShare: (pct) => `PV ${pct}`,
    rte: "Round-trip efficiency",
    rteWait: "Waiting for both medians",
    temperature: "Pack temperature",
    soc: "State of charge",
    tabs: { overview: "Overview", trend: "Trend & windows", lifetime: "Lifetime" },
    waitWindow: "Waiting for a charge or discharge window of at least 20% SoC",
    progress: (direction, done) => `${direction} · ${done} / 20 SoC points`,
    charging: "Charging",
    discharging: "Discharging",
    cycles: "Equivalent cycles",
    cyclesHint: "Grid export / nominal",
    margin: "Vs 6,000-cycle line",
    lastWindow: "Last window",
    noneYet: "None yet",
    lastCharge: "Last charge",
    lastDischarge: "Last discharge",
    nominalLine: "Nominal",
    estimateLine: "Estimate",
    direction: "Direction",
    socRange: "SoC",
    capacity: "Capacity",
    power: "Power",
    duration: "Duration",
    pv: "PV share",
    closed: "Closed",
    noTrend: "Accepted windows will appear here after a 20-point SoC stretch.",
    gridIn: "Grid import",
    gridOut: "Grid export",
    pvEnergy: "PV energy",
    unavailable: "Not reported",
    installed: "Installed",
    explain:
      "Usable capacity is the geometric mean of the discharge median and the charge median, after each direction has three windows of at least 20 percentage points of state of charge. That mean cancels a one-way efficiency shared by both directions. Equivalent cycles are lifetime grid export divided by the nameplate, drawn against a 6,000-cycle LFP line that ends at the 75% warranty floor.",
    editorDevice: "Marstek device",
    editorTitle: "Title",
    editorTab: "Default tab",
    editorCompact: "Compact",
    editorInstalled: "Installation date",
    strategyName: "Marstek battery capacity",
    strategyDescription:
      "State of health, charge and discharge medians, and window progress for every Marstek battery.",
    cardName: "Marstek capacity",
    cardDescription:
      "State of health gauge, charge and discharge medians, and live 20% SoC window progress.",
    overviewName: "Marstek capacity overview",
    overviewDescription: "Compare state of health across every Marstek battery.",
  },
  nl: {
    dashboardTitle: "Batterijcapaciteit",
    overviewTitle: "Marstek-batterijen",
    overviewEmpty: "Nog geen Marstek-batterij met een sensor voor bruikbare capaciteit.",
    pickDevice: "Kies een Marstek-apparaat in de kaarteditor.",
    nominal: "nominaal",
    healthy: "Gezond",
    watch: "Aandacht",
    weak: "Zwak",
    building: (count) => `Opbouwen (${Math.min(count, 3)}/3 vensters)`,
    confidence: {
      low: "Lage betrouwbaarheid",
      medium: "Gemiddelde betrouwbaarheid",
      high: "Hoge betrouwbaarheid",
    },
    windows: (count) => `${count} venster${count === 1 ? "" : "s"}`,
    settings: "Apparaatinstellingen",
    soh: "Staat van gezondheid",
    warranty: "75% garantiegrens",
    spec: "Cyclusspecificatie",
    belowRated: "onder nominaal",
    aboveRated: "boven nominaal",
    atRated: "op nominaal",
    discharge: "Ontlaad-mediaan",
    charge: "Laad-mediaan",
    noWindow: "Nog geen venster",
    pvShare: (pct) => `PV ${pct}`,
    rte: "Round-trip rendement",
    rteWait: "Wacht op beide medianen",
    temperature: "Paktemperatuur",
    soc: "Laadniveau",
    tabs: { overview: "Overzicht", trend: "Trend & vensters", lifetime: "Levensduur" },
    waitWindow: "Wacht op laad-/ontlaadvenster ≥ 20% SoC",
    progress: (direction, done) => `${direction} · ${done} / 20 SoC-punten`,
    charging: "Laden",
    discharging: "Ontladen",
    cycles: "Equivalente cycli",
    cyclesHint: "Netuitvoer / nominaal",
    margin: "T.o.v. 6.000-cyclilijn",
    lastWindow: "Laatste venster",
    noneYet: "Nog geen",
    lastCharge: "Laatste lading",
    lastDischarge: "Laatste ontlading",
    nominalLine: "Nominaal",
    estimateLine: "Schatting",
    direction: "Richting",
    socRange: "SoC",
    capacity: "Capaciteit",
    power: "Vermogen",
    duration: "Duur",
    pv: "PV-aandeel",
    closed: "Gesloten",
    noTrend: "Geaccepteerde vensters verschijnen hier na een traject van 20 SoC-punten.",
    gridIn: "Netinvoer",
    gridOut: "Netuitvoer",
    pvEnergy: "PV-energie",
    unavailable: "Niet gemeld",
    installed: "Geïnstalleerd",
    explain:
      "Bruikbare capaciteit is het meetkundig gemiddelde van de ontlaadmediaan en de laadmediaan, zodra elke richting drie vensters van minstens 20 procentpunten laadniveau heeft. Dat gemiddelde schrapt een eenrichtingsrendement dat voor laden en ontladen gelijk is. Equivalente cycli zijn de levenslange netuitvoer gedeeld door het nominale vermogen, tegen een LFP-lijn van 6.000 cycli die eindigt op de garantiegrens van 75%.",
    editorDevice: "Marstek-apparaat",
    editorTitle: "Titel",
    editorTab: "Standaardtab",
    editorCompact: "Compact",
    editorInstalled: "Installatiedatum",
    strategyName: "Marstek-batterijcapaciteit",
    strategyDescription:
      "Staat van gezondheid, laad- en ontlaadmedianen en venstervoortgang voor elke Marstek-batterij.",
    cardName: "Marstek-capaciteit",
    cardDescription:
      "Meter voor staat van gezondheid, laad- en ontlaadmedianen, en live voortgang van een 20% SoC-venster.",
    overviewName: "Marstek-capaciteitsoverzicht",
    overviewDescription: "Vergelijk de staat van gezondheid van elke Marstek-batterij.",
  },
};

const STYLES = `
  :host { display: block; container-type: inline-size; }
  ha-card { background: var(--ha-card-background, var(--card-background-color)); color: var(--primary-text-color); }
  .body { display: flex; flex-direction: column; gap: 16px; padding: 16px 16px 12px; }
  :host([compact]) .body { gap: 12px; padding: 12px; }
  button, .gauge-hit { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
  button:focus-visible, .gauge-hit:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .nums { font-variant-numeric: tabular-nums; }
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .title { margin: 0; font-size: 1.05rem; font-weight: 650; letter-spacing: -0.01em; }
  .subtitle { margin: 2px 0 0; color: var(--secondary-text-color); font-size: 0.82rem; }
  .head-actions { display: flex; align-items: center; gap: 8px; }
  .pill, .chip, .badge {
    display: inline-flex; align-items: center; gap: 6px;
    border-radius: 999px; padding: 4px 10px; font-size: 0.75rem; font-weight: 650;
    border: 1px solid var(--divider-color);
  }
  .pill ha-icon, .chip ha-icon, .badge ha-icon, .meter-label ha-icon { --mdc-icon-size: 16px; width: 16px; height: 16px; }
  .tone-success { color: var(--success-color, var(--primary-text-color)); background: color-mix(in srgb, var(--success-color, var(--primary-color)) 14%, transparent); }
  .tone-warning { color: var(--warning-color, var(--primary-text-color)); background: color-mix(in srgb, var(--warning-color, var(--primary-color)) 16%, transparent); }
  .tone-error { color: var(--error-color, var(--primary-text-color)); background: color-mix(in srgb, var(--error-color, var(--primary-color)) 14%, transparent); }
  .tone-info { color: var(--info-color, var(--primary-color)); background: color-mix(in srgb, var(--info-color, var(--primary-color)) 14%, transparent); }
  .icon-btn {
    width: 32px; height: 32px; border-radius: 999px; display: grid; place-items: center;
    border: 1px solid var(--divider-color);
  }
  .icon-btn ha-icon { --mdc-icon-size: 18px; width: 18px; height: 18px; }
  .hero { display: grid; grid-template-columns: minmax(168px, 0.86fr) minmax(210px, 1.14fr); gap: 8px 18px; align-items: center; }
  .gauge-hit { width: 100%; text-align: center; }
  .gauge { width: min(100%, 240px); height: auto; overflow: visible; }
  .track-arc { fill: none; stroke: color-mix(in srgb, var(--divider-color) 85%, var(--primary-text-color)); stroke-width: 10; stroke-linecap: round; }
  .value-arc { fill: none; stroke: var(--gauge-color, var(--primary-color)); stroke-width: 10; stroke-linecap: round; }
  .tick { stroke: var(--primary-text-color); stroke-width: 2; }
  .tick.spec { stroke: var(--info-color, var(--primary-color)); stroke-dasharray: 2 2; }
  .soh-num { font-size: 32px; font-weight: 720; fill: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .soh-unit { font-size: 14px; fill: var(--secondary-text-color); }
  .gauge-caption { margin: 0; color: var(--secondary-text-color); font-size: 0.8rem; }
  .stack { display: flex; flex-direction: column; gap: 10px; }
  .meter-top, .meter-meta, .rte, .tracker-label, .foot, .kpi, .legend { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
  .meter-label, .kicker { color: var(--secondary-text-color); font-size: 0.75rem; display: inline-flex; align-items: center; gap: 4px; }
  .meter-value, .rte-value { font-size: 1.05rem; font-weight: 700; }
  .bar { height: 8px; border-radius: 999px; background: color-mix(in srgb, var(--divider-color) 80%, transparent); overflow: hidden; margin-top: 4px; }
  .bar > span { display: block; height: 100%; border-radius: inherit; }
  .bar .discharge { background: var(--info-color, var(--primary-color)); }
  .bar .charge { background: var(--success-color, var(--primary-color)); }
  .meter-meta { color: var(--secondary-text-color); font-size: 0.75rem; margin-top: 3px; }
  .rte { align-items: center; padding-top: 4px; border-top: 1px solid var(--divider-color); }
  .rte-value { font-size: 1.35rem; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
  .chip { color: var(--primary-text-color); background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
  .tabs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px; padding: 3px; border-radius: 999px; background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
  .tabs button { border-radius: 999px; min-height: 32px; color: var(--secondary-text-color); font-size: 0.78rem; font-weight: 650; }
  .tabs button[aria-selected="true"] { background: var(--ha-card-background, var(--card-background-color)); color: var(--primary-text-color); box-shadow: 0 1px 2px color-mix(in srgb, var(--primary-text-color) 18%, transparent); }
  .panel { display: flex; flex-direction: column; gap: 12px; }
  .tracker { display: flex; flex-direction: column; gap: 6px; }
  .soc-line { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: center; color: var(--secondary-text-color); font-size: 0.75rem; }
  .soc-bar { position: relative; height: 14px; border-radius: 999px; background: color-mix(in srgb, var(--divider-color) 75%, transparent); }
  .soc-span { position: absolute; top: 2px; bottom: 2px; border-radius: 999px; background: color-mix(in srgb, var(--primary-color) 55%, transparent); }
  .soc-now { position: absolute; top: -3px; width: 2px; height: 20px; background: var(--primary-text-color); }
  .kpis { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .kpi { flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 10px; border: 1px solid var(--divider-color); border-radius: 12px; }
  .kpi strong { font-size: 1rem; }
  .kpi span { color: var(--secondary-text-color); font-size: 0.72rem; }
  .chart { width: 100%; height: auto; }
  .chart .grid { stroke: var(--divider-color); stroke-width: 1; }
  .chart .nominal { stroke: var(--secondary-text-color); stroke-dasharray: 4 3; fill: none; }
  .chart .estimate { stroke: var(--primary-color); stroke-dasharray: 2 3; fill: none; }
  .chart .discharge { fill: var(--info-color, var(--primary-color)); }
  .chart .charge { fill: var(--success-color, var(--primary-color)); }
  .legend { justify-content: flex-start; flex-wrap: wrap; color: var(--secondary-text-color); font-size: 0.75rem; }
  .swatch { width: 8px; height: 8px; border-radius: 99px; display: inline-block; margin-right: 4px; }
  .table-wrap { max-height: 240px; overflow: auto; border: 1px solid var(--divider-color); border-radius: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.75rem; }
  th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  th { color: var(--secondary-text-color); font-weight: 650; position: sticky; top: 0; background: var(--ha-card-background, var(--card-background-color)); }
  .life-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .explain { margin: 0; color: var(--secondary-text-color); font-size: 0.8rem; line-height: 1.45; }
  .foot { color: var(--secondary-text-color); font-size: 0.72rem; border-top: 1px solid var(--divider-color); padding-top: 8px; }
  .empty { margin: 0; color: var(--secondary-text-color); }
  .rows { display: flex; flex-direction: column; gap: 8px; }
  .row { display: grid; grid-template-columns: 72px 1fr auto; gap: 10px; align-items: center; width: 100%; text-align: left; padding: 8px; border-radius: 12px; border: 1px solid var(--divider-color); }
  .row:hover { background: color-mix(in srgb, var(--primary-color) 6%, transparent); }
  .row .name { font-weight: 650; }
  .row .meta { color: var(--secondary-text-color); font-size: 0.75rem; }
  @container (max-width: 560px) {
    .hero, .life-grid { grid-template-columns: 1fr; }
    .tabs { grid-template-columns: 1fr; }
    .tabs button { min-height: 36px; }
  }
`;

function languageOf(hass) {
  const code = hass?.locale?.language || hass?.language || hass?.config?.language || "en";
  return String(code).toLowerCase().startsWith("nl") ? "nl" : "en";
}

function copyFor(hass) {
  return COPY[languageOf(hass)];
}

function localeOf(hass) {
  return languageOf(hass) === "nl" ? "nl-NL" : "en-GB";
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function finite(value) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(hass, value, digits) {
  if (value == null) return "—";
  return new Intl.NumberFormat(localeOf(hass), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function formatEnergy(hass, wh) {
  const value = finite(wh);
  if (value == null) return "—";
  const abs = Math.abs(value);
  if (abs >= 1000) return `${formatNumber(hass, value / 1000, 2)} kWh`;
  return `${formatNumber(hass, value, 0)} Wh`;
}

function formatPercent(hass, value, digits = 1) {
  const number = finite(value);
  if (number == null) return "—";
  return `${formatNumber(hass, number, digits)}%`;
}

function formatWhen(hass, value) {
  if (value == null || value === "") return null;
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(localeOf(hass), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatDuration(hass, seconds) {
  const value = finite(seconds);
  if (value == null) return "—";
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  if (languageOf(hass) === "nl") {
    return hours > 0 ? `${hours} u ${minutes} min` : `${minutes} min`;
  }
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

function stateNumber(hass, entityId) {
  const state = entityId ? hass?.states?.[entityId] : null;
  if (!state || state.state === "unknown" || state.state === "unavailable") return null;
  return finite(state.state);
}

function entityKind(entity) {
  const unique = entity?.unique_id || "";
  const key = entity?.translation_key || "";
  const pairs = [
    ["capacity", "battery_usable_capacity"],
    ["soh", "battery_state_of_health"],
    ["soc", "battery_soc"],
    ["temp", "bat_temp"],
    ["gridIn", "total_grid_input_energy"],
    ["gridOut", "total_grid_output_energy"],
    ["pv", "total_pv_energy"],
  ];
  for (const [kind, token] of pairs) {
    if (key === token || unique.endsWith(`_${token}`)) return kind;
  }
  return null;
}

function listPacks(hass) {
  const entities = Object.values(hass?.entities || {});
  const grouped = new Map();
  for (const entity of entities) {
    if (entity.platform !== "marstek" || !entity.device_id || entity.disabled_by) continue;
    const kind = entityKind(entity);
    if (!kind) continue;
    const pack = grouped.get(entity.device_id) || { deviceId: entity.device_id, entities: {} };
    pack.entities[kind] = entity.entity_id;
    grouped.set(entity.device_id, pack);
  }
  return [...grouped.values()]
    .filter((pack) => pack.entities.capacity)
    .sort((a, b) => deviceName(hass, a.deviceId).localeCompare(deviceName(hass, b.deviceId)));
}

function deviceName(hass, deviceId) {
  const device = hass?.devices?.[deviceId];
  return device?.name_by_user || device?.name || "";
}

function findPack(hass, config) {
  const packs = listPacks(hass);
  if (config?.device_id) {
    const match = packs.find((pack) => pack.deviceId === config.device_id);
    if (match) return match;
  }
  if (config?.entity) {
    const owner = hass?.entities?.[config.entity];
    if (owner?.device_id) {
      const match = packs.find((pack) => pack.deviceId === owner.device_id);
      if (match) return match;
    }
  }
  return null;
}

function capacityEntityId(hass, config) {
  return findPack(hass, config)?.entities.capacity || config?.entity || "";
}

function isCapacityEntity(hass, entityId) {
  const entity = hass?.entities?.[entityId];
  return entityKind(entity) === "capacity";
}

function normalizeTab(value) {
  return TABS.includes(value) ? value : "overview";
}

function rtePercent(raw) {
  const value = finite(raw);
  if (value == null) return null;
  return value <= 1.5 ? value * 100 : value;
}

function healthOf(text, soh, confidence, dischargeCount, chargeCount) {
  const toward = Math.max(dischargeCount, chargeCount);
  if (confidence === "collecting" || soh == null) {
    return { tone: "info", icon: "mdi:progress-clock", label: text.building(toward) };
  }
  const windows = dischargeCount + chargeCount;
  const confidenceLabel = text.confidence[confidence] || confidence;
  let tone = "success";
  let word = text.healthy;
  let icon = "mdi:shield-check";
  if (soh < WARRANTY_FLOOR) {
    tone = "error";
    word = text.weak;
    icon = "mdi:shield-alert";
  } else if (soh < 90) {
    tone = "warning";
    word = text.watch;
    icon = "mdi:shield-half-full";
  }
  return {
    tone,
    icon,
    label: `${word} · ${confidenceLabel} (${text.windows(windows)})`,
  };
}

function expectedSoh(cycles) {
  if (cycles == null) return null;
  const drop = Math.min(Math.max(cycles, 0) / CYCLE_REFERENCE, 1) * (100 - WARRANTY_FLOOR);
  return 100 - drop;
}

function readModel(hass, config) {
  const pack = findPack(hass, config);
  const capacityId = pack?.entities.capacity || config?.entity;
  const capacity = capacityId ? hass?.states?.[capacityId] : null;
  const attrs = capacity?.attributes || {};
  const device = pack ? hass?.devices?.[pack.deviceId] : null;
  const rated = finite(attrs.rated_capacity_wh);
  const usable = stateNumber(hass, capacityId);
  const soh = stateNumber(hass, pack?.entities.soh) ?? finite(attrs.state_of_health);
  const windows = Array.isArray(attrs.windows) ? attrs.windows : [];
  const chargeWindows = windows.filter((window) => window.direction === "charge");
  const latestCharge = chargeWindows.length ? chargeWindows[chargeWindows.length - 1] : null;
  const exportWh = stateNumber(hass, pack?.entities.gridOut);
  const cycles = rated && exportWh != null ? exportWh / rated : null;
  return {
    pack,
    capacityId,
    attrs,
    device,
    rated,
    usable,
    soh,
    windows,
    pvShare: finite(latestCharge?.pv_share),
    cycles,
    expected: expectedSoh(cycles),
    soc: stateNumber(hass, pack?.entities.soc),
    temp: stateNumber(hass, pack?.entities.temp),
    gridIn: stateNumber(hass, pack?.entities.gridIn),
    gridOut: exportWh,
    pv: stateNumber(hass, pack?.entities.pv),
    rte: rtePercent(attrs.implied_round_trip_efficiency),
    dischargeWh: finite(attrs.discharge_median_wh),
    chargeWh: finite(attrs.charge_median_wh),
    dischargeCount: finite(attrs.discharge_windows_accepted) ?? 0,
    chargeCount: finite(attrs.charge_windows_accepted) ?? 0,
    active: attrs.active_window && typeof attrs.active_window === "object" ? attrs.active_window : null,
    confidence: attrs.confidence || "collecting",
  };
}

function point(cx, cy, radius, degrees) {
  const radians = (degrees * Math.PI) / 180;
  return [cx + radius * Math.cos(radians), cy - radius * Math.sin(radians)];
}

function arcPath(cx, cy, radius, start, end) {
  const [x1, y1] = point(cx, cy, radius, start);
  const [x2, y2] = point(cx, cy, radius, end);
  const large = Math.abs(start - end) > 180 ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${large} 0 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function gaugeSvg(hass, model, text) {
  const cx = 100;
  const cy = 104;
  const radius = 78;
  const soh = model.soh == null ? 0 : Math.max(0, Math.min(100, model.soh));
  // Keep the sweep just under 180° so the upper semicircle stays the short arc.
  const end = soh >= 100 ? 0.4 : 180 - (soh / 100) * 180;
  const value = soh > 0 ? arcPath(cx, cy, radius, 180, end) : "";
  const warranty = point(cx, cy, radius, 180 - (WARRANTY_FLOOR / 100) * 180);
  const warrantyOuter = point(cx, cy, radius + 8, 180 - (WARRANTY_FLOOR / 100) * 180);
  let specMark = "";
  if (model.expected != null) {
    const angle = 180 - (model.expected / 100) * 180;
    const inner = point(cx, cy, radius - 12, angle);
    const outer = point(cx, cy, radius + 8, angle);
    specMark = `<line class="tick spec" x1="${inner[0].toFixed(1)}" y1="${inner[1].toFixed(1)}" x2="${outer[0].toFixed(1)}" y2="${outer[1].toFixed(1)}" />`;
  }
  const tone = model.soh == null ? "var(--divider-color)" : model.soh < WARRANTY_FLOOR ? "var(--error-color, var(--primary-color))" : model.soh < 90 ? "var(--warning-color, var(--primary-color))" : "var(--success-color, var(--primary-color))";
  const delta = model.usable != null && model.rated != null ? model.usable - model.rated : null;
  let deltaText = "";
  if (delta != null) {
    const word = delta < -1 ? text.belowRated : delta > 1 ? text.aboveRated : text.atRated;
    deltaText = `${formatEnergy(hass, Math.abs(delta))} ${word}`;
  }
  return `
    <button class="gauge-hit" data-action="more-info" aria-label="${esc(text.soh)}">
      <svg class="gauge" viewBox="0 0 200 132" style="--gauge-color: ${tone}">
        <path class="track-arc" d="${arcPath(cx, cy, radius, 180, 0)}" />
        ${value ? `<path class="value-arc" d="${value}" />` : ""}
        <line class="tick" x1="${warranty[0].toFixed(1)}" y1="${warranty[1].toFixed(1)}" x2="${warrantyOuter[0].toFixed(1)}" y2="${warrantyOuter[1].toFixed(1)}" />
        ${specMark}
        <text class="soh-num" x="100" y="96" text-anchor="middle">${esc(model.soh == null ? "—" : formatNumber(hass, model.soh, 1))}</text>
        <text class="soh-unit" x="100" y="114" text-anchor="middle">%</text>
      </svg>
      <p class="gauge-caption nums">${esc(formatEnergy(hass, model.usable))}${deltaText ? ` · ${esc(deltaText)}` : ""}</p>
      <p class="gauge-caption">${esc(text.warranty)}${model.expected != null ? ` · ${esc(text.spec)}` : ""}</p>
    </button>`;
}

function meterRow(hass, text, label, icon, wh, rated, count, tone, glyph, extra) {
  const scale = rated && rated > 0 ? Math.max(0, Math.min(100, ((wh || 0) / rated) * 100)) : wh ? 100 : 0;
  const countLabel = wh == null ? text.noWindow : `${glyph} ${text.windows(count)}`;
  return `
    <div class="meter">
      <div class="meter-top">
        <span class="meter-label"><ha-icon icon="${icon}"></ha-icon>${esc(label)}</span>
        <span class="meter-value nums">${esc(formatEnergy(hass, wh))}</span>
      </div>
      <div class="bar" aria-hidden="true"><span class="${tone}" style="width:${scale.toFixed(1)}%"></span></div>
      <div class="meter-meta nums"><span>${esc(countLabel)}</span>${extra || ""}</div>
    </div>`;
}

function trackerHtml(hass, model, text) {
  const active = model.active;
  if (!active) {
    return `<p class="empty">${esc(text.waitWindow)}</p>`;
  }
  const start = finite(active.start_soc) ?? 0;
  const current = finite(active.current_soc) ?? start;
  const left = Math.max(0, Math.min(100, Math.min(start, current)));
  const right = Math.max(0, Math.min(100, Math.max(start, current)));
  const direction = active.direction === "charge" ? text.charging : text.discharging;
  const done = formatNumber(hass, finite(active.soc_delta) ?? 0, 1);
  return `
    <div class="tracker">
      <div class="tracker-label"><span>${esc(text.progress(direction, done))}</span><span class="nums">${esc(formatEnergy(hass, active.accumulated_wh))}</span></div>
      <div class="soc-line nums">
        <span>0%</span>
        <div class="soc-bar" aria-hidden="true">
          <div class="soc-span" style="left:${left.toFixed(1)}%;width:${Math.max(1.5, right - left).toFixed(1)}%"></div>
          <div class="soc-now" style="left:${Math.max(0, Math.min(100, current)).toFixed(1)}%"></div>
        </div>
        <span>100%</span>
      </div>
    </div>`;
}

function kpiHtml(hass, model, text) {
  const margin = model.soh != null && model.expected != null ? model.soh - model.expected : null;
  const marginText = margin == null ? "—" : `${margin > 0 ? "+" : ""}${formatNumber(hass, margin, 1)} pt`;
  const last = formatWhen(hass, model.attrs.last_window_at) || text.noneYet;
  return `
    <div class="kpis">
      <div class="kpi"><strong class="nums">${esc(model.cycles == null ? "—" : formatNumber(hass, model.cycles, model.cycles < 100 ? 1 : 0))}</strong><span>${esc(text.cycles)}</span><span>${esc(text.cyclesHint)}</span></div>
      <div class="kpi"><strong class="nums">${esc(marginText)}</strong><span>${esc(text.margin)}</span></div>
      <div class="kpi"><strong class="nums">${esc(formatPercent(hass, model.rte, 1))}</strong><span>${esc(text.rte)}</span></div>
      <div class="kpi"><strong class="nums">${esc(last)}</strong><span>${esc(text.lastWindow)}</span></div>
    </div>`;
}

function chartSvg(hass, model, text) {
  if (!model.windows.length) return `<p class="empty">${esc(text.noTrend)}</p>`;
  const width = 640;
  const height = 168;
  const pad = { l: 44, r: 10, t: 12, b: 18 };
  const times = model.windows.map((window) => finite(window.closed_at) ?? 0);
  const values = model.windows.map((window) => finite(window.capacity_wh) ?? 0);
  const references = [model.rated, model.usable].filter((value) => value != null);
  const minY = Math.min(...values, ...references) * 0.92;
  const maxY = Math.max(...values, ...references) * 1.05;
  const minX = Math.min(...times);
  const maxX = Math.max(...times);
  const xOf = (time) => pad.l + ((maxX === minX ? 0.5 : (time - minX) / (maxX - minX)) * (width - pad.l - pad.r));
  const yOf = (wh) => pad.t + ((maxY - wh) / (maxY - minY || 1)) * (height - pad.t - pad.b);
  const line = (wh) => {
    if (wh == null) return "";
    const y = yOf(wh);
    return `<line class="nominal" x1="${pad.l}" y1="${y.toFixed(1)}" x2="${width - pad.r}" y2="${y.toFixed(1)}" />`;
  };
  const marks = model.windows.map((window) => {
    const x = xOf(finite(window.closed_at) ?? minX);
    const y = yOf(finite(window.capacity_wh) ?? minY);
    if (window.direction === "charge") {
      return `<rect class="charge" x="${(x - 4).toFixed(1)}" y="${(y - 4).toFixed(1)}" width="8" height="8" transform="rotate(45 ${x.toFixed(1)} ${y.toFixed(1)})" />`;
    }
    return `<circle class="discharge" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4" />`;
  });
  const estimate = model.usable == null ? "" : `<line class="estimate" x1="${pad.l}" y1="${yOf(model.usable).toFixed(1)}" x2="${width - pad.r}" y2="${yOf(model.usable).toFixed(1)}" />`;
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img">
      <line class="grid" x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${height - pad.b}" />
      ${line(model.rated)}
      ${estimate}
      ${marks.join("")}
    </svg>
    <div class="legend">
      <span><i class="swatch" style="background: var(--info-color, var(--primary-color))"></i>${esc(text.discharge)}</span>
      <span><i class="swatch" style="background: var(--success-color, var(--primary-color)); transform: rotate(45deg)"></i>${esc(text.charge)}</span>
      <span>${esc(text.nominalLine)}</span>
      <span>${esc(text.estimateLine)}</span>
    </div>`;
}

function tableHtml(hass, model, text) {
  if (!model.windows.length) return "";
  const rows = [...model.windows].reverse().map((window) => {
    const direction = window.direction === "charge" ? text.charging : text.discharging;
    const start = finite(window.start_soc);
    const end = finite(window.end_soc);
    const range = start == null || end == null ? "—" : `${formatNumber(hass, start, 0)}→${formatNumber(hass, end, 0)}%`;
    const pv = finite(window.pv_share);
    return `<tr>
      <td>${esc(direction)}</td>
      <td class="nums">${esc(range)}</td>
      <td class="nums">${esc(formatEnergy(hass, window.capacity_wh))}</td>
      <td class="nums">${esc(finite(window.average_power_w) == null ? "—" : `${formatNumber(hass, window.average_power_w, 0)} W`)}</td>
      <td class="nums">${esc(formatDuration(hass, window.duration_s))}</td>
      <td class="nums">${esc(pv == null || pv <= 0 ? "—" : formatPercent(hass, pv * 100, 0))}</td>
      <td class="nums">${esc(formatWhen(hass, window.closed_at) || "—")}</td>
    </tr>`;
  });
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>${esc(text.direction)}</th><th>${esc(text.socRange)}</th><th>${esc(text.capacity)}</th>
          <th>${esc(text.power)}</th><th>${esc(text.duration)}</th><th>${esc(text.pv)}</th><th>${esc(text.closed)}</th>
        </tr></thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>`;
}

function lifetimeHtml(hass, model, text, installed) {
  const cells = [
    [text.gridIn, model.gridIn],
    [text.gridOut, model.gridOut],
    [text.pvEnergy, model.pv],
  ].map(([label, value]) => `
    <div class="kpi"><strong class="nums">${esc(value == null ? text.unavailable : formatEnergy(hass, value))}</strong><span>${esc(label)}</span></div>`);
  const width = 640;
  const height = 120;
  const maxCycles = Math.max(CYCLE_REFERENCE, model.cycles || 0);
  const xOf = (cycles) => 36 + (cycles / maxCycles) * (width - 48);
  const yOf = (soh) => 16 + ((100 - soh) / 40) * (height - 36);
  const dot = model.cycles != null && model.soh != null
    ? `<circle cx="${xOf(model.cycles).toFixed(1)}" cy="${yOf(Math.max(60, Math.min(100, model.soh))).toFixed(1)}" r="5" fill="var(--primary-color)" />`
    : "";
  const installedLine = installed ? `<p class="gauge-caption">${esc(text.installed)} · <span class="nums">${esc(installed)}</span></p>` : "";
  return `
    <div class="life-grid">${cells.join("")}</div>
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(text.margin)}">
      <line class="nominal" x1="${xOf(0)}" y1="${yOf(100)}" x2="${xOf(CYCLE_REFERENCE)}" y2="${yOf(WARRANTY_FLOOR)}" />
      <line class="grid" x1="${xOf(CYCLE_REFERENCE)}" y1="${yOf(100)}" x2="${xOf(CYCLE_REFERENCE)}" y2="${yOf(60)}" />
      ${dot}
      <text class="soh-unit" x="36" y="14">100%</text>
      <text class="soh-unit" x="${xOf(CYCLE_REFERENCE) - 28}" y="${height - 4}">${CYCLE_REFERENCE}</text>
    </svg>
    ${installedLine}
    <p class="explain">${esc(text.explain)}</p>`;
}

function cardHtml(hass, config, tab) {
  const text = copyFor(hass);
  const model = hass ? readModel(hass, config) : null;
  if (!model?.capacityId) {
    return `<style>${STYLES}</style><ha-card><div class="body"><p class="empty">${esc(text.pickDevice)}</p></div></ha-card>`;
  }
  const title = config.title || deviceName(hass, model.pack?.deviceId) || model.capacityId;
  const modelName = model.device?.model ? `${model.device.model} · ` : "";
  const nominal = model.rated == null ? "" : `${formatEnergy(hass, model.rated)} ${text.nominal}`;
  const health = healthOf(text, model.soh, model.confidence, model.dischargeCount, model.chargeCount);
  const pv = model.pvShare != null && model.pvShare > 0
    ? `<span class="badge tone-info"><ha-icon icon="mdi:solar-power"></ha-icon>${esc(text.pvShare(formatPercent(hass, model.pvShare * 100, 0)))}</span>`
    : "";
  const temp = model.temp == null ? "" : `<span class="chip"><ha-icon icon="mdi:thermometer"></ha-icon><span class="nums">${esc(formatNumber(hass, model.temp, 1))} °C</span></span>`;
  const soc = model.soc == null ? "" : `<span class="chip"><ha-icon icon="mdi:battery-heart"></ha-icon><span class="nums">${esc(formatPercent(hass, model.soc, 0))}</span></span>`;
  const panel = tab === "trend"
    ? `${chartSvg(hass, model, text)}${tableHtml(hass, model, text)}`
    : tab === "lifetime"
      ? lifetimeHtml(hass, model, text, config.installation_date)
      : `${trackerHtml(hass, model, text)}${kpiHtml(hass, model, text)}`;
  const tabs = config.compact ? "" : `
    <div class="tabs" role="tablist">
      ${TABS.map((name) => `<button type="button" role="tab" data-action="tab" data-tab="${name}" aria-selected="${name === tab}">${esc(text.tabs[name])}</button>`).join("")}
    </div>
    <div class="panel" role="tabpanel">${panel}</div>`;
  const foot = config.compact ? "" : `
    <div class="foot">
      <span>${esc(text.lastCharge)} · <span class="nums">${esc(formatWhen(hass, model.attrs.last_charge_at) || text.noneYet)}</span></span>
      <span>${esc(text.lastDischarge)} · <span class="nums">${esc(formatWhen(hass, model.attrs.last_discharge_at) || text.noneYet)}</span></span>
    </div>`;
  return `
    <style>${STYLES}</style>
    <ha-card>
      <div class="body"
        data-soh="${esc(model.soh ?? "")}"
        data-usable="${esc(model.usable ?? "")}"
        data-discharge="${esc(model.dischargeWh ?? "")}"
        data-charge="${esc(model.chargeWh ?? "")}"
        data-rte="${esc(model.rte ?? "")}">
        <header class="head">
          <div>
            <h3 class="title">${esc(title)}</h3>
            <p class="subtitle nums">${esc(`${modelName}${nominal}`.trim())}</p>
          </div>
          <div class="head-actions">
            <span class="pill tone-${health.tone}"><ha-icon icon="${health.icon}"></ha-icon>${esc(health.label)}</span>
            <button class="icon-btn" type="button" data-action="settings" aria-label="${esc(text.settings)}"><ha-icon icon="mdi:cog"></ha-icon></button>
          </div>
        </header>
        <section class="hero">
          ${gaugeSvg(hass, model, text)}
          <div class="stack">
            ${meterRow(hass, text, text.discharge, "mdi:arrow-down-bold", model.dischargeWh, model.rated, model.dischargeCount, "discharge", "↓", "")}
            ${meterRow(hass, text, text.charge, "mdi:arrow-up-bold", model.chargeWh, model.rated, model.chargeCount, "charge", "↑", pv)}
            <div class="rte">
              <div>
                <div class="kicker">${esc(text.rte)}</div>
                <div class="rte-value nums">${esc(model.rte == null ? "—" : formatPercent(hass, model.rte, 1))}</div>
                ${model.rte == null ? `<div class="kicker">${esc(text.rteWait)}</div>` : ""}
              </div>
              <div class="chips">${temp}${soc}</div>
            </div>
          </div>
        </section>
        ${tabs}
        ${foot}
      </div>
    </ha-card>`;
}

function overviewHtml(hass) {
  const text = copyFor(hass);
  const packs = listPacks(hass);
  const rows = packs.map((pack) => {
    const model = readModel(hass, { device_id: pack.deviceId, entity: pack.entities.capacity });
    const health = healthOf(text, model.soh, model.confidence, model.dischargeCount, model.chargeCount);
    return `
      <button class="row" type="button" data-action="more-info" data-entity="${esc(pack.entities.capacity)}">
        <span class="nums" style="font-size:1.2rem;font-weight:720">${esc(model.soh == null ? "—" : formatNumber(hass, model.soh, 0))}<span style="font-size:0.7rem">%</span></span>
        <span><span class="name">${esc(deviceName(hass, pack.deviceId) || pack.entities.capacity)}</span><br><span class="meta nums">${esc(formatEnergy(hass, model.usable))} · ${esc(formatPercent(hass, model.rte, 0))}</span></span>
        <span class="pill tone-${health.tone}">${esc(health.label)}</span>
      </button>`;
  });
  const body = rows.length ? `<div class="rows">${rows.join("")}</div>` : `<p class="empty">${esc(text.overviewEmpty)}</p>`;
  return `<style>${STYLES}</style><ha-card><div class="body"><h3 class="title">${esc(text.overviewTitle)}</h3>${body}</div></ha-card>`;
}

function openMoreInfo(host, entityId) {
  if (!entityId) return;
  const event = new Event("hass-action", { bubbles: true, composed: true });
  event.detail = {
    config: { entity: entityId, tap_action: { action: "more-info" } },
    action: "tap",
  };
  host.dispatchEvent(event);
}

function openDevice(host, deviceId) {
  if (!deviceId) return;
  history.pushState(null, "", `/config/devices/device/${deviceId}`);
  const event = new CustomEvent("location-changed", {
    bubbles: true,
    composed: true,
    detail: { replace: false },
  });
  host.dispatchEvent(event);
}

class MarstekCapacityCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._tab = "overview";
    this._signature = "";
    this.shadowRoot.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action]");
      if (!target) return;
      const action = target.dataset.action;
      if (action === "tab") {
        this._tab = normalizeTab(target.dataset.tab);
        this._signature = "";
        this._render();
        return;
      }
      if (action === "settings") {
        openDevice(this, findPack(this._hass, this._config)?.deviceId || this._config.device_id);
        return;
      }
      if (action === "more-info") {
        const pack = findPack(this._hass, this._config);
        openMoreInfo(this, pack?.entities.soh || pack?.entities.capacity || this._config.entity);
      }
    });
  }

  static getConfigElement() {
    return document.createElement("marstek-capacity-card-editor");
  }

  static getStubConfig(hass, entities, entitiesFallback) {
    const prefer = [...(entities || []), ...(entitiesFallback || [])];
    const listed = prefer.find((entityId) => isCapacityEntity(hass, entityId));
    const scanned = listed || Object.keys(hass?.states || {}).find((entityId) => isCapacityEntity(hass, entityId));
    const config = { default_tab: "overview", compact: false };
    if (!scanned) return config;
    config.entity = scanned;
    const deviceId = hass.entities?.[scanned]?.device_id;
    if (deviceId) config.device_id = deviceId;
    return config;
  }

  setConfig(config) {
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      throw new Error("Marstek capacity card configuration must be an object");
    }
    this._config = { default_tab: "overview", compact: false, ...config };
    this._tab = normalizeTab(this._config.default_tab);
    if (this._config.compact) this.setAttribute("compact", "");
    else this.removeAttribute("compact");
    this._signature = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    return this._config.compact ? 4 : 8;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const tokens = this._hass
      ? listPacks(this._hass).flatMap((pack) => Object.values(pack.entities).map((entityId) => {
          const state = this._hass.states[entityId];
          const active = state?.attributes?.active_window;
          return `${entityId}:${state?.state}:${state?.attributes?.last_window_at}:${active?.soc_delta ?? ""}:${active?.current_soc ?? ""}`;
        }))
      : [];
    const signature = JSON.stringify({ tab: this._tab, config: this._config, tokens });
    if (signature === this._signature) return;
    this._signature = signature;
    this.shadowRoot.innerHTML = cardHtml(this._hass, this._config, this._tab);
  }
}

class MarstekCapacityOverviewCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this.shadowRoot.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action='more-info']");
      if (target) openMoreInfo(this, target.dataset.entity);
    });
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    if (this.shadowRoot) this.shadowRoot.innerHTML = overviewHtml(hass);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  getCardSize() {
    return 3;
  }
}

class MarstekCapacityCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { default_tab: "overview", compact: false, ...(config || {}) };
    this._built = false;
    if (this.isConnected) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (this.isConnected) this._build();
    for (const selector of this.querySelectorAll("ha-selector")) selector.hass = hass;
  }

  connectedCallback() {
    this._build();
  }

  _build() {
    if (this._built || !this._hass || !this._config) return;
    this._built = true;
    this.replaceChildren();
    const text = copyFor(this._hass);
    const style = document.createElement("style");
    style.textContent = `
      marstek-capacity-card-editor .field { display: flex; flex-direction: column; gap: 4px; margin: 0 0 12px; }
      marstek-capacity-card-editor label { color: var(--secondary-text-color); font-size: 0.8rem; }
    `;
    this.append(style);
    const fields = [
      ["device_id", text.editorDevice, { device: { integration: "marstek" } }, this._config.device_id || ""],
      ["title", text.editorTitle, { text: {} }, this._config.title || ""],
      ["default_tab", text.editorTab, { select: { mode: "dropdown", options: TABS.map((tab) => ({ value: tab, label: text.tabs[tab] })) } }, normalizeTab(this._config.default_tab)],
      ["compact", text.editorCompact, { boolean: {} }, Boolean(this._config.compact)],
      ["installation_date", text.editorInstalled, { date: {} }, this._config.installation_date || ""],
    ];
    for (const [key, label, selector, value] of fields) {
      const field = document.createElement("div");
      field.className = "field";
      const caption = document.createElement("label");
      caption.textContent = label;
      const control = document.createElement("ha-selector");
      control.hass = this._hass;
      control.selector = selector;
      control.value = value;
      control.label = label;
      control.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        this._commit(key, event.detail.value);
      });
      field.append(caption, control);
      this.append(field);
    }
  }

  _commit(key, value) {
    const next = { ...this._config, [key]: value };
    if (key === "device_id") {
      const pack = listPacks(this._hass).find((item) => item.deviceId === value);
      if (pack) next.entity = pack.entities.capacity;
    }
    if (value === "" || value == null) delete next[key];
    this._config = next;
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config: next },
      bubbles: true,
      composed: true,
    }));
  }
}

class MarstekCapacityStrategy extends HTMLElement {
  static registryDependencies = ["devices", "entities"];

  static async generate(_config, hass) {
    const text = copyFor(hass);
    const packs = listPacks(hass);
    const sections = [
      {
        type: "grid",
        column_span: 2,
        cards: [{ type: `custom:${OVERVIEW_TYPE}` }],
      },
      ...packs.map((pack) => ({
        type: "grid",
        cards: [{
          type: `custom:${CARD_TYPE}`,
          device_id: pack.deviceId,
          entity: pack.entities.capacity,
        }],
      })),
    ];
    return {
      views: [{
        title: text.dashboardTitle,
        path: "capacity",
        type: "sections",
        max_columns: 2,
        sections,
      }],
    };
  }
}

function registerCards() {
  const text = COPY.en;
  window.customCards = window.customCards || [];
  const known = new Set(window.customCards.map((card) => card.type));
  const cards = [
    {
      type: CARD_TYPE,
      name: text.cardName,
      description: text.cardDescription,
      preview: true,
      documentationURL: "https://github.com/taurgis/has-marstek-local-api/blob/main/docs/usable-capacity.md",
    },
    {
      type: OVERVIEW_TYPE,
      name: text.overviewName,
      description: text.overviewDescription,
      preview: true,
      documentationURL: "https://github.com/taurgis/has-marstek-local-api/blob/main/docs/usable-capacity.md",
    },
  ];
  for (const card of cards) {
    if (!known.has(card.type)) window.customCards.push(card);
  }
  window.customStrategies = window.customStrategies || [];
  if (!window.customStrategies.some((item) => item.type === STRATEGY_TYPE)) {
    window.customStrategies.push({
      type: STRATEGY_TYPE,
      strategyType: "dashboard",
      name: text.strategyName,
      description: text.strategyDescription,
      documentationURL: "https://github.com/taurgis/has-marstek-local-api/blob/main/docs/usable-capacity.md",
    });
  }
}

if (!customElements.get(CARD_TYPE)) customElements.define(CARD_TYPE, MarstekCapacityCard);
if (!customElements.get(OVERVIEW_TYPE)) customElements.define(OVERVIEW_TYPE, MarstekCapacityOverviewCard);
if (!customElements.get("marstek-capacity-card-editor")) {
  customElements.define("marstek-capacity-card-editor", MarstekCapacityCardEditor);
}
if (!customElements.get("ll-strategy-dashboard-marstek-capacity")) {
  customElements.define("ll-strategy-dashboard-marstek-capacity", MarstekCapacityStrategy);
}
registerCards();
