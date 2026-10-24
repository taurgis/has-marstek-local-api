// Marstek capacity dossier. Loaded by the integration; no Lovelace resource.
// Sections grid: contributors.home-assistant.io/frontend/custom-ui/card/
// Strategies: contributors.home-assistant.io/frontend/custom-ui/strategy/
//
// Elements are defined from defineAll(), after <home-assistant> exists.
// Home Assistant installs a scoped customElements registry at that moment.
// Defining ll-strategy-dashboard-marstek-capacity earlier puts it on the
// previous registry, and a cold visit to /marstek-capacity times out.

const CARD_TYPE = "marstek-capacity-card";
const OVERVIEW_TYPE = "marstek-capacity-overview-card";
const STRATEGY_TYPE = "marstek-capacity";
const WARRANTY_FLOOR = 75;
const CYCLE_REFERENCE = 6000;
const TABS = ["overview", "trend", "windows", "lifetime"];

const COPY = {
  en: {
    dashboardTitle: "Battery Capacity",
    overviewTitle: "Marstek batteries",
    overviewEmpty: "No Marstek battery with a usable-capacity sensor is set up yet.",
    pickDevice: "Choose a Marstek device in the card editor.",
    capacityWord: "Capacity",
    healthy: "Healthy",
    watch: "Watch",
    belowWarranty: "Below warranty",
    calibrating: "Calibrating",
    confidence: { low: "low confidence", medium: "medium confidence", high: "high confidence" },
    nominal: "Nominal",
    usable: "Usable",
    medians: "Discharge / charge",
    expected: (pct) => `100% · exp. ${pct}`,
    sohLine: (soh, delta) => `${soh} SoH (${delta})`,
    medianLine: (rte, count) => (rte ? `RTE ${rte} · ${count}` : count),
    windowWord: (count) => `${count} window${count === 1 ? "" : "s"}`,
    warrantyMark: "Warranty floor 75%",
    specMark: (pct, margin) => (margin ? `Cycle spec ${pct} (${margin})` : `Cycle spec ${pct}`),
    aboveFloor: "above 75%",
    belowFloor: "below 75%",
    margin: (energy, side) => `Margin ${energy} ${side}`,
    soh: "State of health",
    monitor: (discharge, charge, confidence) =>
      `Monitor active · ${discharge} discharge and ${charge} charge windows (${confidence})`,
    monitorWait: "Calibrating · waiting for a 20-point SoC window",
    activeLine: (direction, power, start, current, done, energy) =>
      `${direction}${power} · window ${start} → ${current} (${done} / 20 SoC points · ${energy})`,
    charging: "Charging",
    discharging: "Discharging",
    busyCharge: "Charging",
    busyDischarge: "Discharging",
    idle: "Idle",
    settings: "Device settings",
    tabs: { overview: "Overview", trend: "Trend", windows: "Windows", lifetime: "Lifetime" },
    cycles: "cycles",
    pvShare: (pct) => `PV share ${pct}`,
    lastDischarge: "Last discharge window",
    lastCharge: "Last charge window",
    noneYet: "none yet",
    nominalLine: "Nominal",
    estimateLine: "Usable estimate",
    warrantyLine: "75% warranty",
    dischargeMark: "Discharge median",
    chargeMark: "Charge median",
    noTrend: "Accepted windows will appear here after a 20-point SoC stretch.",
    noHistory: "Weekly snapshots appear after the first estimate or energy total.",
    historyTitle: "Usable capacity by week",
    gridIn: "Grid import",
    gridOut: "Grid export",
    pvEnergy: "PV energy",
    loadEnergy: "Load energy",
    unavailable: "Not reported",
    installed: "Installed",
    explain: "Usable capacity is the geometric mean of the two medians, once each direction has three windows of at least 20 SoC points.",
    colWindow: "Window",
    colCap: "100% cap.",
    colPower: "Power · time",
    colPv: "PV",
    colClosed: "Closed",
    colWeek: "Week",
    colMedians: "↓ / ↑ kWh",
    colRte: "RTE",
    battery: "Battery",
    grid: "Grid",
    meter: "CT",
    pv: "PV",
    export: "Export",
    import: "Import",
    live: "Live flow",
    fleet: "Batteries",
    fleetLive: "Live",
    published: (done, total) => `${done} of ${total} published`,
    mix: (charging, discharging) => {
      const parts = [];
      if (charging) parts.push(`${charging} charging`);
      if (discharging) parts.push(`${discharging} discharging`);
      return parts.join(" · ") || "Idle";
    },
    rte: (pct) => `RTE ${pct}`,
    soc: (pct) => `SoC ${pct}`,
    windowProgress: (done, target) => `${done}/${target} SoC`,
    modes: { auto: "Auto", manual: "Manual", passive: "Passive", ai: "AI", ups: "UPS" },
    editorDevice: "Marstek device",
    editorTitle: "Title",
    editorFlow: "Live energy flow",
    editorWindow: "Active window",
    editorTabs: "Tabs",
    editorTab: "Default tab",
    editorCompact: "Compact",
    editorInstalled: "Installation date",
    strategyName: "Marstek battery capacity",
    strategyDescription: "State of health, charge and discharge medians, and window progress for every Marstek battery.",
    cardName: "Marstek capacity",
    cardDescription: "Usable capacity, charge and discharge medians, and live 20% SoC window progress.",
    overviewName: "Marstek capacity overview",
    overviewDescription: "Compare state of health across every Marstek battery.",
  },
  nl: {
    dashboardTitle: "Batterijcapaciteit",
    overviewTitle: "Marstek-batterijen",
    overviewEmpty: "Nog geen Marstek-batterij met een sensor voor bruikbare capaciteit.",
    pickDevice: "Kies een Marstek-apparaat in de kaarteditor.",
    capacityWord: "Capaciteit",
    healthy: "Goed",
    watch: "Aandacht",
    belowWarranty: "Onder specificatie",
    calibrating: "Kalibreren",
    confidence: { low: "lage betrouwbaarheid", medium: "gemiddeld betrouwbaar", high: "hoog betrouwbaar" },
    nominal: "Opgegeven",
    usable: "Bruikbaar",
    medians: "Ontladen / laden",
    expected: (pct) => `100% · verw. ${pct}`,
    sohLine: (soh, delta) => `${soh} SoH (${delta})`,
    medianLine: (rte, count) => (rte ? `RTE ${rte} · ${count}` : count),
    windowWord: (count) => `${count} venster${count === 1 ? "" : "s"}`,
    warrantyMark: "Garantiedrempel 75%",
    specMark: (pct, margin) => (margin ? `Specificatie ${pct} (${margin})` : `Specificatie ${pct}`),
    aboveFloor: "boven 75%",
    belowFloor: "onder 75%",
    margin: (energy, side) => `Marge ${energy} ${side}`,
    soh: "Gezondheid",
    monitor: (discharge, charge, confidence) =>
      `Meting actief · ${discharge} ontlaad- en ${charge} laadvensters (${confidence})`,
    monitorWait: "Kalibreren · wacht op een venster van 20 SoC-punten",
    activeLine: (direction, power, start, current, done, energy) =>
      `${direction}${power} · venster ${start} → ${current} (${done} / 20 SoC-punten · ${energy})`,
    charging: "Laden",
    discharging: "Ontladen",
    busyCharge: "Bezig met laden",
    busyDischarge: "Bezig met ontladen",
    idle: "Rust",
    settings: "Apparaatinstellingen",
    tabs: { overview: "Overzicht", trend: "Trend", windows: "Vensters", lifetime: "Levensduur" },
    cycles: "cycli",
    pvShare: (pct) => `PV-aandeel ${pct}`,
    lastDischarge: "Laatste ontlaadvenster",
    lastCharge: "Laatste laadvenster",
    noneYet: "nog geen",
    nominalLine: "Nominaal",
    estimateLine: "Schatting bruikbaar",
    warrantyLine: "75% garantie",
    dischargeMark: "Ontlaadmediaan",
    chargeMark: "Laadmediaan",
    noTrend: "Geaccepteerde vensters verschijnen hier na een traject van 20 SoC-punten.",
    noHistory: "Wekelijkse momenten verschijnen na de eerste schatting of energieteller.",
    historyTitle: "Bruikbare capaciteit per week",
    gridIn: "Netinvoer",
    gridOut: "Netuitvoer",
    pvEnergy: "PV-energie",
    loadEnergy: "Verbruik",
    unavailable: "Niet gemeld",
    installed: "Geïnstalleerd",
    explain: "Bruikbare capaciteit is het meetkundig gemiddelde van de twee medianen, zodra elke richting drie vensters van minstens 20 SoC-punten heeft.",
    colWindow: "Venster",
    colCap: "100% cap.",
    colPower: "W · duur",
    colPv: "PV",
    colClosed: "Gesloten",
    colWeek: "Week",
    colMedians: "↓ / ↑ kWh",
    colRte: "RTE",
    battery: "Batterij",
    grid: "Net",
    meter: "CT",
    pv: "PV",
    export: "Uitvoer",
    import: "Invoer",
    live: "Live stroom",
    fleet: "Batterijen",
    fleetLive: "Live",
    published: (done, total) => `${done} van ${total} bekend`,
    mix: (charging, discharging) => {
      const parts = [];
      if (charging) parts.push(`${charging} laden`);
      if (discharging) parts.push(`${discharging} ontladen`);
      return parts.join(" · ") || "Rust";
    },
    rte: (pct) => `RTE ${pct}`,
    soc: (pct) => `SoC ${pct}`,
    windowProgress: (done, target) => `${done}/${target} SoC`,
    modes: { auto: "Auto", manual: "Handmatig", passive: "Passief", ai: "AI", ups: "UPS" },
    editorDevice: "Marstek-apparaat",
    editorTitle: "Titel",
    editorFlow: "Live energiestroom",
    editorWindow: "Actief venster",
    editorTabs: "Tabbladen",
    editorTab: "Standaardtab",
    editorCompact: "Compact",
    editorInstalled: "Installatiedatum",
    strategyName: "Marstek-batterijcapaciteit",
    strategyDescription: "Staat van gezondheid, laad- en ontlaadmedianen en venstervoortgang voor elke Marstek-batterij.",
    cardName: "Marstek-capaciteit",
    cardDescription: "Bruikbare capaciteit, laad- en ontlaadmedianen, en live voortgang van een 20% SoC-venster.",
    overviewName: "Marstek-capaciteitsoverzicht",
    overviewDescription: "Vergelijk de staat van gezondheid van elke Marstek-batterij.",
  },
};

const STYLES = `
  :host { display: block; container-type: inline-size; }
  ha-card { background: var(--ha-card-background, var(--card-background-color)); color: var(--primary-text-color); }
  .body { display: flex; flex-direction: column; gap: 10px; padding: 14px 14px 12px; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .nums { font-variant-numeric: tabular-nums; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.3; min-width: 0; }
  .head-actions { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
  .pill {
    display: inline-flex; align-items: center; border-radius: 12px; padding: 3px 10px;
    font-size: 12px; font-weight: 600; line-height: 16px; white-space: nowrap;
    border: 1px solid var(--divider-color);
  }
  .tone-success { color: var(--success-color, var(--primary-text-color)); background: color-mix(in srgb, var(--success-color, #2e7d32) 16%, transparent); }
  .tone-warning { color: var(--warning-color, var(--primary-text-color)); background: color-mix(in srgb, var(--warning-color, #e65100) 16%, transparent); }
  .tone-error { color: var(--error-color, var(--primary-text-color)); background: color-mix(in srgb, var(--error-color, #d32f2f) 16%, transparent); }
  .tone-info { color: var(--info-color, var(--primary-color)); background: color-mix(in srgb, var(--info-color, var(--primary-color)) 14%, transparent); }
  .icon-btn { width: 28px; height: 28px; border-radius: 999px; display: grid; place-items: center; border: 1px solid var(--divider-color); }
  .icon-btn ha-icon { --mdc-icon-size: 16px; width: 16px; height: 16px; }
  .mon { display: flex; gap: 8px; align-items: flex-start; margin: 0; font-size: 12px; line-height: 1.4; }
  .dot { width: 8px; height: 8px; border-radius: 99px; margin-top: 4px; flex: 0 0 auto; background: var(--dot, var(--success-color, #2e7d32)); }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .pills button {
    display: inline-flex; flex: 0 0 auto; align-items: center; width: auto;
    border: 1px solid var(--divider-color); border-radius: 12px; padding: 3px 12px;
    font-size: 12px; line-height: 16px; color: var(--primary-text-color);
  }
  .pills button[aria-selected="true"] { background: color-mix(in srgb, var(--primary-color) 18%, transparent); border-color: transparent; font-weight: 600; }
  .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .tile { background: var(--secondary-background-color); border-radius: 8px; padding: 8px 10px; min-width: 0; text-align: left; }
  .tile .lbl, .tile .sub, .flow-cell .k, .flow-cell .s { display: block; font-size: 11px; line-height: 1.3; color: var(--secondary-text-color); }
  .tile .val { display: block; margin-top: 2px; font-size: 16px; font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
  .tile .sub { margin-top: 2px; }
  .val.success { color: var(--success-color, #2e7d32); }
  .val.warning { color: var(--warning-color, #e65100); }
  .val.error { color: var(--error-color, #d32f2f); }
  .track { position: relative; height: 10px; border-radius: 5px; background: rgba(128, 128, 128, 0.2); margin: 14px 0 6px; }
  .fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px; background: var(--bar, var(--success-color, #2e7d32)); max-width: 100%; }
  .mark { position: absolute; width: 2px; top: -4px; bottom: -4px; }
  .mark.floor { background: var(--error-color, #d32f2f); }
  .mark.spec { background: var(--primary-text-color); }
  .scale { display: flex; justify-content: space-between; gap: 12px; font-size: 11px; line-height: 1.35; color: var(--secondary-text-color); }
  .scale span { min-width: 0; }
  .scale .floor { color: var(--error-color, #d32f2f); }
  .summary { margin: 2px 0 0; font-size: 12px; line-height: 1.4; }
  .foot, .explain, .empty { margin: 0; font-size: 11px; line-height: 1.4; color: var(--secondary-text-color); }
  .flow { display: grid; gap: 6px; }
  .flow-cell { background: var(--secondary-background-color); border-radius: 8px; padding: 6px 8px; min-width: 0; }
  .flow-cell .v { display: block; font-size: 12px; font-weight: 600; line-height: 1.3; }
  .flow-cell .s { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chart { width: 100%; height: auto; display: block; }
  .chart .grid { stroke: var(--divider-color); stroke-width: 1; }
  .chart .axis { fill: var(--secondary-text-color); font-size: 11px; }
  .chart .nominal { stroke: var(--secondary-text-color); stroke-dasharray: 5 4; fill: none; stroke-width: 1.5; }
  .chart .estimate { stroke: var(--primary-color); stroke-dasharray: 1.5 3; fill: none; stroke-width: 1.5; }
  .chart .warranty { stroke: var(--error-color, #d32f2f); stroke-dasharray: 5 4; fill: none; stroke-width: 1.5; }
  .chart .discharge { fill: var(--info-color, var(--primary-color)); }
  .chart .charge { fill: var(--success-color, #2e7d32); }
  .legend { display: flex; flex-wrap: wrap; gap: 8px 12px; color: var(--secondary-text-color); font-size: 11px; align-items: center; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .swatch { width: 8px; height: 8px; border-radius: 99px; display: inline-block; }
  .swatch.diamond { border-radius: 1px; transform: rotate(45deg); }
  .swatch.dashed, .swatch.dotted, .swatch.warranty { width: 16px; height: 0; border-radius: 0; background: none; }
  .swatch.dashed { border-top: 2px dashed var(--secondary-text-color); }
  .swatch.dotted { border-top: 2px dotted var(--primary-color); }
  .swatch.warranty { border-top: 2px dashed var(--error-color, #d32f2f); }
  .table-wrap { overflow: hidden; }
  table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 11px; }
  th, td { padding: 4px 3px; border-bottom: 1px solid var(--divider-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  th { color: var(--secondary-text-color); font-weight: 600; text-align: left; }
  td.num, th.num { text-align: right; }
  .life-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
  .rows { display: flex; flex-direction: column; gap: 8px; }
  .row {
    display: grid; grid-template-columns: 52px minmax(0, 1fr) auto; gap: 10px; align-items: center;
    width: 100%; text-align: left; padding: 8px; border-radius: 8px; background: var(--secondary-background-color);
  }
  .row .soh { font-size: 18px; font-weight: 600; }
  .row .name { display: block; font-weight: 600; font-size: 13px; }
  .row .meta { display: block; color: var(--secondary-text-color); font-size: 11px; line-height: 1.35; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @container (max-width: 420px) {
    .life-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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

function signedEnergy(hass, wh) {
  const value = finite(wh);
  if (value == null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatEnergy(hass, Math.abs(value))}`;
}

function watts(hass, value) {
  const number = finite(value);
  if (number == null) return null;
  return `${formatNumber(hass, number, 0)} W`;
}

function absWatts(hass, value) {
  const number = finite(value);
  if (number == null) return null;
  return `${formatNumber(hass, Math.abs(number), 0)} W`;
}

function parseDate(value) {
  if (value == null || value === "") return null;
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatClosed(hass, value) {
  const date = parseDate(value);
  if (!date) return "—";
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const options = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  if (!sameYear) options.year = "numeric";
  return new Intl.DateTimeFormat(localeOf(hass), options).format(date);
}

function formatAxisDate(hass, value) {
  const date = parseDate(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(localeOf(hass), { day: "numeric", month: "short" }).format(date);
}

function formatDurationCompact(seconds) {
  const value = finite(seconds);
  if (value == null) return "—";
  return `${Math.max(0, Math.round(value / 60))}m`;
}

function joinParts(parts) {
  return parts.filter((part) => part != null && part !== "").join(" · ");
}

function stateOf(hass, entityId) {
  const state = entityId ? hass?.states?.[entityId] : null;
  if (!state || state.state === "unknown" || state.state === "unavailable") return null;
  return state;
}

function stateNumber(hass, entityId) {
  return finite(stateOf(hass, entityId)?.state);
}

function stateText(hass, entityId) {
  const state = stateOf(hass, entityId);
  return state ? state.state : null;
}

const CARD_DEFAULTS = {
  default_tab: "overview",
  compact_mode: false,
  show_realtime_flow: true,
  show_active_window: true,
  show_tabs: true,
};

function enabled(config, key, fallback) {
  const value = config?.[key];
  return typeof value === "boolean" ? value : fallback;
}

function compactOf(config) {
  return Boolean(config?.compact_mode || config?.compact);
}

function withDefaults(config) {
  return { ...CARD_DEFAULTS, ...(config || {}) };
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
    ["load", "total_load_energy"],
    ["power", "battery_power"],
    ["status", "battery_status"],
    ["ongrid", "ongrid_power"],
    ["offgrid", "offgrid_power"],
    ["pvPower", "pv_power"],
    ["mode", "device_mode"],
    ["rssi", "wifi_rssi"],
    ["phaseA", "em_a_power"],
    ["phaseB", "em_b_power"],
    ["phaseC", "em_c_power"],
    ["phaseTotal", "em_total_power"],
    ["pv1", "pv1_power"],
    ["pv2", "pv2_power"],
    ["pv3", "pv3_power"],
    ["pv4", "pv4_power"],
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

function expectedSoh(cycles) {
  if (cycles == null) return null;
  const drop = Math.min(Math.max(cycles, 0) / CYCLE_REFERENCE, 1) * (100 - WARRANTY_FLOOR);
  return 100 - drop;
}

function toneOf(soh) {
  if (soh == null) return "info";
  if (soh < WARRANTY_FLOOR) return "error";
  if (soh < 90) return "warning";
  return "success";
}

function healthOf(text, soh, confidence) {
  if (confidence === "collecting" || soh == null) {
    return { tone: "info", label: text.calibrating };
  }
  if (soh < WARRANTY_FLOOR) return { tone: "error", label: text.belowWarranty };
  if (soh < 90) return { tone: "warning", label: text.watch };
  return { tone: "success", label: text.healthy };
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
  const chargeWindows = windows.filter((item) => item.direction === "charge");
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
    load: stateNumber(hass, pack?.entities.load),
    power: stateNumber(hass, pack?.entities.power),
    status: stateText(hass, pack?.entities.status),
    ongrid: stateNumber(hass, pack?.entities.ongrid),
    offgrid: stateNumber(hass, pack?.entities.offgrid),
    pvPower: stateNumber(hass, pack?.entities.pvPower),
    mode: stateText(hass, pack?.entities.mode),
    rssi: stateNumber(hass, pack?.entities.rssi),
    phaseA: stateNumber(hass, pack?.entities.phaseA),
    phaseB: stateNumber(hass, pack?.entities.phaseB),
    phaseC: stateNumber(hass, pack?.entities.phaseC),
    phaseTotal: stateNumber(hass, pack?.entities.phaseTotal),
    pv1: stateNumber(hass, pack?.entities.pv1),
    pv2: stateNumber(hass, pack?.entities.pv2),
    pv3: stateNumber(hass, pack?.entities.pv3),
    pv4: stateNumber(hass, pack?.entities.pv4),
    history: Array.isArray(attrs.history) ? attrs.history : [],
    rte: rtePercent(attrs.implied_round_trip_efficiency),
    dischargeWh: finite(attrs.discharge_median_wh),
    chargeWh: finite(attrs.charge_median_wh),
    dischargeCount: finite(attrs.discharge_windows_accepted) ?? 0,
    chargeCount: finite(attrs.charge_windows_accepted) ?? 0,
    active: attrs.active_window && typeof attrs.active_window === "object" ? attrs.active_window : null,
    confidence: attrs.confidence || "collecting",
  };
}

function flowDirection(text, model) {
  if (model.status === "charging" || (model.power != null && model.power < -20)) return "charge";
  if (model.status === "discharging" || (model.power != null && model.power > 20)) return "discharge";
  return "idle";
}

function directionWord(text, direction) {
  if (direction === "charge") return text.charging;
  if (direction === "discharge") return text.discharging;
  return text.idle;
}

function cardTitle(hass, config, model, text) {
  if (config?.title) return config.title;
  const name = deviceName(hass, model.pack?.deviceId) || "Marstek";
  return `${name} · ${text.capacityWord}`;
}

function monitorHtml(hass, model, text, showWindow) {
  const health = healthOf(text, model.soh, model.confidence);
  const active = showWindow ? model.active : null;
  let sentence;
  if (active && (finite(active.soc_delta) ?? 0) > 0) {
    const busy = active.direction === "charge" ? text.busyCharge : text.busyDischarge;
    const power = model.power == null ? "" : ` (${formatNumber(hass, model.power, 0)} W)`;
    sentence = text.activeLine(
      busy,
      power,
      formatNumber(hass, finite(active.start_soc) ?? 0, 0) + "%",
      formatNumber(hass, finite(active.current_soc) ?? 0, 0) + "%",
      formatNumber(hass, finite(active.soc_delta) ?? 0, 1),
      formatEnergy(hass, active.accumulated_wh),
    );
  } else if (model.dischargeCount + model.chargeCount === 0) {
    sentence = text.monitorWait;
  } else {
    const confidence = text.confidence[model.confidence] || text.confidence.medium;
    sentence = text.monitor(model.dischargeCount, model.chargeCount, confidence);
  }
  const extra = active
    ? ""
    : joinParts([
        text.modes[model.mode] || "",
        model.temp == null ? "" : `${formatNumber(hass, model.temp, 0)} °C`,
        model.rssi == null ? "" : `${formatNumber(hass, model.rssi, 0)} dBm`,
      ]);
  const line = extra ? `${sentence} · ${extra}` : sentence;
  return `<p class="mon"><i class="dot" style="--dot: var(--${health.tone === "info" ? "info-color" : health.tone === "success" ? "success-color" : health.tone === "warning" ? "warning-color" : "error-color"}, var(--primary-color))"></i><span>${esc(line)}</span></p>`;
}

function tile(label, value, sub, tone) {
  const color = tone ? ` ${tone}` : "";
  return `<div class="tile"><span class="lbl">${esc(label)}</span><strong class="val nums${color}">${esc(value)}</strong><span class="sub nums">${esc(sub)}</span></div>`;
}

function overviewPanel(hass, model, text) {
  const health = healthOf(text, model.soh, model.confidence);
  const expected = model.expected == null ? "—" : formatPercent(hass, model.expected, 1);
  const deltaWh = model.usable != null && model.rated != null ? model.usable - model.rated : null;
  const sohText = model.soh == null ? "—" : formatPercent(hass, model.soh, 1);
  const usableSub = model.soh == null ? text.calibrating : text.sohLine(sohText, signedEnergy(hass, deltaWh));
  const discharge = model.dischargeWh == null ? "—" : `↓ ${formatNumber(hass, model.dischargeWh / 1000, 2)}`;
  const charge = model.chargeWh == null ? "—" : `↑ ${formatNumber(hass, model.chargeWh / 1000, 2)}`;
  const medianValue = model.dischargeWh == null && model.chargeWh == null ? "—" : `${discharge} / ${charge} kWh`;
  const rte = model.rte == null ? "" : formatPercent(hass, model.rte, 1);
  const count = text.windowWord(model.dischargeCount + model.chargeCount);
  const soh = model.soh == null ? 0 : Math.max(0, Math.min(100, model.soh));
  const spec = model.expected == null ? null : Math.max(0, Math.min(100, model.expected));
  const floorWh = model.rated == null ? null : model.rated * (WARRANTY_FLOOR / 100);
  const marginWh = model.usable != null && floorWh != null ? model.usable - floorWh : null;
  const specMargin = model.usable != null && model.rated != null && model.expected != null
    ? model.usable - model.rated * (model.expected / 100)
    : null;
  const barColor = health.tone === "error" ? "var(--error-color)" : health.tone === "warning" ? "var(--warning-color)" : "var(--success-color)";
  const specTick = spec == null ? "" : `<i class="mark spec" style="left:${spec.toFixed(1)}%"></i>`;
  const summary = joinParts([
    marginWh == null ? "" : text.margin(signedEnergy(hass, marginWh), marginWh >= 0 ? text.aboveFloor : text.belowFloor),
    model.cycles == null ? "" : `${formatNumber(hass, model.cycles, model.cycles < 10 ? 1 : 0)} ${text.cycles}`,
    rte ? `RTE ${rte}` : "",
    model.pvShare != null && model.pvShare > 0 ? text.pvShare(formatPercent(hass, model.pvShare * 100, 0)) : "",
  ]);
  const nl = languageOf(hass) === "nl";
  const marginWord = nl ? "marge" : "margin";
  return `
    <div class="tiles">
      ${tile(text.nominal, formatEnergy(hass, model.rated), text.expected(expected))}
      ${tile(text.usable, formatEnergy(hass, model.usable), usableSub, model.soh == null ? "" : health.tone)}
      ${tile(text.medians, medianValue, text.medianLine(rte, count))}
    </div>
    <div>
      <div class="track" style="--bar: ${barColor}" aria-hidden="true">
        <i class="fill" style="width:${soh.toFixed(1)}%"></i>
        <i class="mark floor" style="left:${WARRANTY_FLOOR}%"></i>
        ${specTick}
      </div>
      <div class="scale">
        <span class="floor">▎ ${esc(text.warrantyMark)}</span>
        <span>▎ ${esc(text.specMark(expected, specMargin == null ? "" : `${signedEnergy(hass, specMargin)} ${marginWord}`))}</span>
      </div>
    </div>
    <p class="summary nums">${esc(summary)}</p>`;
}

function flowCell(label, value, detail) {
  if (value == null) return "";
  const extra = detail ? `<span class="s nums">${esc(detail)}</span>` : "";
  return `<div class="flow-cell"><span class="k">${esc(label)}</span><strong class="v nums">${esc(value)}</strong>${extra}</div>`;
}

function flowHtml(hass, model, text) {
  const direction = directionWord(text, flowDirection(text, model));
  const soc = model.soc == null ? direction : joinParts([direction, formatPercent(hass, model.soc, 0)]);
  const gridWord = model.ongrid == null ? "" : model.ongrid > 20 ? text.export : model.ongrid < -20 ? text.import : text.idle;
  const phases = [["A", model.phaseA], ["B", model.phaseB], ["C", model.phaseC]].filter((entry) => entry[1] != null);
  const meter = watts(hass, model.phaseTotal) || (phases.length ? phases.map(([name, value]) => `${name} ${formatNumber(hass, value, 0)}`).join(" ") : null);
  const channels = [model.pv1, model.pv2, model.pv3, model.pv4].filter((value) => value != null).length;
  const cells = [
    flowCell(text.battery, absWatts(hass, model.power), soc),
    flowCell(text.grid, watts(hass, model.ongrid), gridWord),
    flowCell(text.meter, meter, ""),
    flowCell(text.pv, watts(hass, model.pvPower), channels ? `${channels} MPPT` : ""),
  ].filter(Boolean);
  if (!cells.length) return "";
  return `<section class="flow" style="grid-template-columns: repeat(${cells.length}, minmax(0, 1fr))" aria-label="${esc(text.live)}">${cells.join("")}</section>`;
}

function axisTicks(min, max, count) {
  const span = max - min || 1;
  const step = span / (count - 1);
  return Array.from({ length: count }, (_item, index) => min + step * index);
}

function chartSvg(hass, model, text) {
  if (!model.windows.length) return `<p class="empty">${esc(text.noTrend)}</p>`;
  const width = 360;
  const height = 168;
  const pad = { l: 44, r: 12, t: 14, b: 22 };
  const values = model.windows.map((item) => finite(item.capacity_wh) ?? 0);
  const references = [model.rated, model.usable].filter((value) => value != null);
  const minY = Math.min(...values, ...references) * 0.96;
  const maxY = Math.max(...values, ...references) * 1.04;
  const times = model.windows.map((item) => finite(item.closed_at) ?? 0);
  const minX = Math.min(...times);
  const maxX = Math.max(...times);
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const xOf = (time) => {
    const ratio = maxX === minX ? 0.5 : (time - minX) / (maxX - minX);
    return pad.l + 8 + ratio * (plotW - 16);
  };
  const yOf = (wh) => pad.t + ((maxY - wh) / (maxY - minY || 1)) * plotH;
  const ticks = axisTicks(minY, maxY, 4);
  const grid = ticks.map((wh) => {
    const y = yOf(wh);
    const label = formatNumber(hass, wh / 1000, 2);
    return `<line class="grid" x1="${pad.l}" y1="${y.toFixed(1)}" x2="${width - pad.r}" y2="${y.toFixed(1)}" /><text class="axis" x="${pad.l - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end">${esc(label)}</text>`;
  }).join("");
  const labelAt = [0, Math.floor((times.length - 1) / 2), times.length - 1];
  const xLabels = [...new Set(labelAt)].map((index) => {
    const x = xOf(times[index]);
    return `<text class="axis" x="${x.toFixed(1)}" y="${height - 4}" text-anchor="middle">${esc(formatAxisDate(hass, times[index]))}</text>`;
  }).join("");
  const horizontal = (wh, klass) => {
    if (wh == null) return "";
    const y = yOf(wh);
    return `<line class="${klass}" x1="${pad.l}" y1="${y.toFixed(1)}" x2="${width - pad.r}" y2="${y.toFixed(1)}" />`;
  };
  const marks = model.windows.map((item) => {
    const x = xOf(finite(item.closed_at) ?? minX);
    const y = yOf(finite(item.capacity_wh) ?? minY);
    if (item.direction === "charge") {
      return `<rect class="charge" x="${(x - 3.5).toFixed(1)}" y="${(y - 3.5).toFixed(1)}" width="7" height="7" transform="rotate(45 ${x.toFixed(1)} ${y.toFixed(1)})" />`;
    }
    return `<circle class="discharge" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" />`;
  }).join("");
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(text.historyTitle)}">
      ${grid}
      ${horizontal(model.rated, "nominal")}
      ${horizontal(model.usable, "estimate")}
      ${marks}
      ${xLabels}
    </svg>
    <div class="legend">
      <span><i class="swatch" style="background: var(--info-color, var(--primary-color))"></i>${esc(text.dischargeMark)}</span>
      <span><i class="swatch diamond" style="background: var(--success-color, #2e7d32)"></i>${esc(text.chargeMark)}</span>
      <span><i class="swatch dashed"></i>${esc(text.nominalLine)}</span>
      <span><i class="swatch dotted"></i>${esc(text.estimateLine)}</span>
    </div>`;
}

function tableHtml(hass, model, text) {
  if (!model.windows.length) return `<p class="empty">${esc(text.noTrend)}</p>`;
  const rows = [...model.windows].reverse().map((item) => {
    const arrow = item.direction === "charge" ? "↑" : "↓";
    const start = finite(item.start_soc);
    const end = finite(item.end_soc);
    const range = start == null || end == null ? "—" : `${formatNumber(hass, start, 0)}→${formatNumber(hass, end, 0)}%`;
    const pv = finite(item.pv_share);
    const power = finite(item.average_power_w);
    const timing = joinParts([
      power == null ? "" : `${formatNumber(hass, Math.abs(power), 0)} W`,
      formatDurationCompact(item.duration_s),
    ]);
    return `<tr>
      <td>${esc(`${arrow} ${range}`)}</td>
      <td class="num">${esc(formatEnergy(hass, item.capacity_wh))}</td>
      <td class="num">${esc(timing || "—")}</td>
      <td class="num">${esc(pv == null || pv <= 0 ? "—" : formatPercent(hass, pv * 100, 0))}</td>
      <td class="num">${esc(formatClosed(hass, item.closed_at))}</td>
    </tr>`;
  });
  return `
    <div class="table-wrap">
      <table>
        <colgroup>
          <col style="width:20%" /><col style="width:18%" /><col style="width:28%" /><col style="width:10%" /><col style="width:24%" />
        </colgroup>
        <thead><tr>
          <th>${esc(text.colWindow)}</th>
          <th class="num">${esc(text.colCap)}</th>
          <th class="num">${esc(text.colPower)}</th>
          <th class="num">${esc(text.colPv)}</th>
          <th class="num">${esc(text.colClosed)}</th>
        </tr></thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>`;
}

function historyHtml(hass, model, text) {
  const points = Array.isArray(model.history) ? model.history : [];
  if (!points.length) return `<p class="empty">${esc(text.noHistory)}</p>`;
  const rows = [...points].reverse().slice(0, 12).map((item) => {
    const rte = rtePercent(item.implied_round_trip_efficiency);
    const cycles = finite(item.equivalent_cycles);
    const kwh = (wh) => {
      const value = finite(wh);
      return value == null ? "—" : formatNumber(hass, value / 1000, 2);
    };
    const medians = `${kwh(item.discharge_median_wh)} / ${kwh(item.charge_median_wh)}`;
    const rteText = joinParts([
      rte == null ? "" : formatPercent(hass, rte, 1),
      cycles == null ? "" : formatNumber(hass, cycles, 1),
    ]);
    return `<tr>
      <td class="nums">${esc(item.period || item.month || "—")}</td>
      <td class="num">${esc(formatEnergy(hass, item.usable_wh))}</td>
      <td class="num">${esc(medians)}</td>
      <td class="num">${esc(rteText || "—")}</td>
    </tr>`;
  });
  return `
    <div>
      <p class="foot">${esc(text.historyTitle)}</p>
      <div class="table-wrap">
        <table>
          <colgroup>
            <col style="width:24%" /><col style="width:22%" /><col style="width:30%" /><col style="width:24%" />
          </colgroup>
          <thead><tr>
            <th>${esc(text.colWeek)}</th>
            <th class="num">${esc(text.usable)}</th>
            <th class="num">${esc(text.colMedians)}</th>
            <th class="num">${esc(text.colRte)}</th>
          </tr></thead>
          <tbody>${rows.join("")}</tbody>
        </table>
      </div>
    </div>`;
}

function lifetimeHtml(hass, model, text, installed) {
  const cells = [
    [text.gridIn, model.gridIn],
    [text.gridOut, model.gridOut],
    [text.pvEnergy, model.pv],
    [text.loadEnergy, model.load],
  ].map(([label, value]) => tile(label, value == null ? text.unavailable : formatEnergy(hass, value), ""));
  const width = 360;
  const height = 168;
  const pad = { l: 44, r: 16, t: 16, b: 22 };
  const maxCycles = Math.max(8, (model.cycles || 0) * 1.6);
  const minY = 68;
  const maxY = 104;
  const xOf = (cycles) => pad.l + 8 + (cycles / maxCycles) * (width - pad.l - pad.r - 16);
  const yOf = (soh) => pad.t + ((maxY - soh) / (maxY - minY)) * (height - pad.t - pad.b);
  const yTicks = [70, 80, 90, 100].map((soh) => {
    const y = yOf(soh);
    return `<line class="grid" x1="${pad.l}" y1="${y.toFixed(1)}" x2="${width - pad.r}" y2="${y.toFixed(1)}" /><text class="axis" x="${pad.l - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end">${soh}%</text>`;
  }).join("");
  const xTicks = [0, maxCycles / 2, maxCycles].map((cycles) => {
    const x = xOf(cycles);
    const label = formatNumber(hass, cycles, cycles < 10 ? 0 : 0);
    return `<text class="axis" x="${x.toFixed(1)}" y="${height - 4}" text-anchor="middle">${esc(label)}</text>`;
  }).join("");
  const floorY = yOf(WARRANTY_FLOOR);
  const expectedEnd = expectedSoh(maxCycles) ?? WARRANTY_FLOOR;
  const dot = model.cycles != null && model.soh != null
    ? `<circle cx="${xOf(model.cycles).toFixed(1)}" cy="${yOf(Math.max(minY, Math.min(maxY, model.soh))).toFixed(1)}" r="4" fill="var(--primary-color)" />`
    : "";
  const installedLine = installed ? `<p class="foot">${esc(text.installed)} · <span class="nums">${esc(installed)}</span></p>` : "";
  return `
    <div class="life-grid">${cells.join("")}</div>
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(text.warrantyLine)}">
      ${yTicks}
      <line class="warranty" x1="${pad.l}" y1="${floorY.toFixed(1)}" x2="${width - pad.r}" y2="${floorY.toFixed(1)}" />
      <text class="axis" x="${width - pad.r}" y="${(floorY - 4).toFixed(1)}" text-anchor="end">75%</text>
      <line class="nominal" x1="${xOf(0).toFixed(1)}" y1="${yOf(100).toFixed(1)}" x2="${xOf(maxCycles).toFixed(1)}" y2="${yOf(expectedEnd).toFixed(1)}" />
      ${dot}
      ${xTicks}
    </svg>
    <div class="legend">
      <span><i class="swatch warranty"></i>${esc(text.warrantyLine)}</span>
      <span><i class="swatch dashed"></i>${esc(text.nominalLine)}</span>
      <span><i class="swatch" style="background: var(--primary-color)"></i>${esc(text.estimateLine)}</span>
    </div>
    ${installedLine}
    <p class="explain">${esc(text.explain)}</p>`;
}

function footerHtml(hass, model, text) {
  return `<p class="foot nums">${esc(text.lastDischarge)}: ${esc(formatClosed(hass, model.attrs.last_discharge_at) === "—" ? text.noneYet : formatClosed(hass, model.attrs.last_discharge_at))} · ${esc(text.lastCharge)}: ${esc(formatClosed(hass, model.attrs.last_charge_at) === "—" ? text.noneYet : formatClosed(hass, model.attrs.last_charge_at))}</p>`;
}

function cardHtml(hass, config, tab) {
  const text = copyFor(hass);
  const model = hass ? readModel(hass, config) : null;
  if (!model?.capacityId) {
    return `<style>${STYLES}</style><ha-card><div class="body"><p class="empty">${esc(text.pickDevice)}</p></div></ha-card>`;
  }
  const health = healthOf(text, model.soh, model.confidence);
  const showFlow = enabled(config, "show_realtime_flow", true);
  const showWindow = enabled(config, "show_active_window", true);
  const showTabs = enabled(config, "show_tabs", true) && !compactOf(config);
  const selected = showTabs ? normalizeTab(tab) : "overview";
  const pills = showTabs ? `
    <div class="pills" role="tablist">
      ${TABS.map((name) => `<button type="button" role="tab" data-action="tab" data-tab="${name}" aria-selected="${name === selected}">${esc(text.tabs[name])}</button>`).join("")}
    </div>` : "";
  const panel = selected === "trend"
    ? `${chartSvg(hass, model, text)}${historyHtml(hass, model, text)}`
    : selected === "windows"
      ? tableHtml(hass, model, text)
      : selected === "lifetime"
        ? `${lifetimeHtml(hass, model, text, config.installation_date)}${historyHtml(hass, model, text)}`
        : overviewPanel(hass, model, text);
  const flow = showFlow && selected === "overview" ? flowHtml(hass, model, text) : "";
  return `
    <style>${STYLES}</style>
    <ha-card>
      <div class="body" data-soh="${esc(model.soh ?? "")}" data-usable="${esc(model.usable ?? "")}" data-rte="${esc(model.rte ?? "")}">
        <header class="head">
          <h3 class="title">${esc(cardTitle(hass, config, model, text))}</h3>
          <div class="head-actions">
            <span class="pill tone-${health.tone}">${esc(health.label)}</span>
            <button class="icon-btn" type="button" data-action="settings" aria-label="${esc(text.settings)}"><ha-icon icon="mdi:cog"></ha-icon></button>
          </div>
        </header>
        ${monitorHtml(hass, model, text, showWindow)}
        ${pills}
        ${flow}
        <div class="panel" role="tabpanel">${panel}</div>
        ${footerHtml(hass, model, text)}
      </div>
    </ha-card>`;
}

function overviewHtml(hass) {
  const text = copyFor(hass);
  const packs = listPacks(hass);
  const models = packs.map((pack) => readModel(hass, { device_id: pack.deviceId, entity: pack.entities.capacity }));
  const sohs = models.map((model) => model.soh).filter((value) => value != null);
  const powers = models.map((model) => model.power).filter((value) => value != null);
  const average = sohs.length ? sohs.reduce((sum, value) => sum + value, 0) / sohs.length : null;
  const combined = powers.length ? powers.reduce((sum, value) => sum + value, 0) : null;
  const charging = models.filter((model) => flowDirection(text, model) === "charge").length;
  const discharging = models.filter((model) => flowDirection(text, model) === "discharge").length;
  const header = `
    <div class="tiles">
      ${tile(text.fleet, String(packs.length), text.fleetLive)}
      ${tile(text.soh, average == null ? "—" : formatPercent(hass, average, 0), text.published(sohs.length, packs.length), average == null ? "" : toneOf(average))}
      ${tile(text.battery, combined == null ? "—" : `${formatNumber(hass, combined, 0)} W`, text.mix(charging, discharging))}
    </div>`;
  const rows = packs.map((pack, index) => {
    const model = models[index];
    const health = healthOf(text, model.soh, model.confidence);
    const primary = joinParts([
      model.power == null ? "" : `${absWatts(hass, model.power)} ${directionWord(text, flowDirection(text, model)).toLowerCase()}`,
      text.modes[model.mode] || "",
      model.temp == null ? "" : `${formatNumber(hass, model.temp, 0)} °C`,
    ]);
    const active = model.active
      ? text.windowProgress(formatNumber(hass, finite(model.active.soc_delta) ?? 0, 0), formatNumber(hass, finite(model.active.target_soc_delta) || 20, 0))
      : "";
    const secondary = joinParts([
      model.soc == null ? "" : text.soc(formatPercent(hass, model.soc, 0)),
      model.rte == null ? "" : text.rte(formatPercent(hass, model.rte, 0)),
      active,
    ]);
    return `
      <button class="row" type="button" data-action="more-info" data-entity="${esc(pack.entities.capacity)}">
        <span class="soh nums">${esc(model.soh == null ? "—" : formatNumber(hass, model.soh, 0))}<span style="font-size:11px">%</span></span>
        <span>
          <span class="name">${esc(deviceName(hass, pack.deviceId) || pack.entities.capacity)}</span>
          <span class="meta nums">${esc(primary || formatEnergy(hass, model.usable))}</span>
          ${secondary ? `<span class="meta nums">${esc(secondary)}</span>` : ""}
        </span>
        <span class="pill tone-${health.tone}">${esc(health.label)}</span>
      </button>`;
  });
  const body = rows.length ? `<div class="rows">${rows.join("")}</div>` : `<p class="empty">${esc(text.overviewEmpty)}</p>`;
  return `<style>${STYLES}</style><ha-card><div class="body"><h3 class="title">${esc(text.overviewTitle)}</h3>${header}${body}</div></ha-card>`;
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
    const config = { ...CARD_DEFAULTS };
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
    this._config = withDefaults(config);
    this._tab = normalizeTab(this._config.default_tab);
    if (compactOf(this._config)) this.setAttribute("compact", "");
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
    if (compactOf(this._config)) return 4;
    return enabled(this._config, "show_tabs", true) ? 6 : 5;
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
          const history = state?.attributes?.history;
          const tail = Array.isArray(history) && history.length ? history[history.length - 1] : null;
          return `${entityId}:${state?.state}:${state?.attributes?.last_window_at}:${active?.soc_delta ?? ""}:${active?.current_soc ?? ""}:${active?.accumulated_wh ?? ""}:${tail?.recorded_at ?? ""}:${tail?.usable_wh ?? ""}`;
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
    this._config = withDefaults(config);
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
      marstek-capacity-card-editor .field > span { color: var(--secondary-text-color); font-size: 12px; }
    `;
    this.append(style);
    const fields = [
      ["device_id", text.editorDevice, { device: { integration: "marstek" } }, this._config.device_id || ""],
      ["title", text.editorTitle, { text: {} }, this._config.title || ""],
      ["show_realtime_flow", text.editorFlow, { boolean: {} }, enabled(this._config, "show_realtime_flow", true)],
      ["show_active_window", text.editorWindow, { boolean: {} }, enabled(this._config, "show_active_window", true)],
      ["show_tabs", text.editorTabs, { boolean: {} }, enabled(this._config, "show_tabs", true)],
      ["default_tab", text.editorTab, { select: { mode: "dropdown", options: TABS.map((tab) => ({ value: tab, label: text.tabs[tab] })) } }, normalizeTab(this._config.default_tab)],
      ["compact_mode", text.editorCompact, { boolean: {} }, compactOf(this._config)],
      ["installation_date", text.editorInstalled, { date: {} }, this._config.installation_date || ""],
    ];
    for (const [key, caption, selector, value] of fields) {
      const field = document.createElement("div");
      field.className = "field";
      const name = document.createElement("span");
      name.textContent = caption;
      const control = document.createElement("ha-selector");
      control.hass = this._hass;
      control.selector = selector;
      control.value = value;
      control.required = false;
      control.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        this._commit(key, event.detail.value);
      });
      field.append(name, control);
      this.append(field);
    }
  }

  _commit(key, value) {
    const next = { ...this._config, [key]: value };
    if (key === "device_id") {
      const pack = listPacks(this._hass).find((item) => item.deviceId === value);
      if (pack) next.entity = pack.entities.capacity;
    }
    if (key === "compact_mode") delete next.compact;
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
          show_realtime_flow: true,
          show_active_window: true,
          show_tabs: true,
          default_tab: "overview",
          compact_mode: false,
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

function defineAll() {
  const registry = (typeof window !== "undefined" && window.customElements) || customElements;
  const define = (name, ctor) => {
    if (!registry.get(name)) registry.define(name, ctor);
  };
  define(CARD_TYPE, MarstekCapacityCard);
  define(OVERVIEW_TYPE, MarstekCapacityOverviewCard);
  define("marstek-capacity-card-editor", MarstekCapacityCardEditor);
  define("ll-strategy-dashboard-marstek-capacity", MarstekCapacityStrategy);
  registerCards();
}

function bootCapacityCard() {
  const registry = (typeof window !== "undefined" && window.customElements) || customElements;
  if (registry.get("home-assistant")) {
    defineAll();
    return;
  }
  if (registry.whenDefined) {
    registry.whenDefined("home-assistant").then(defineAll);
    setTimeout(defineAll, 2000);
    return;
  }
  defineAll();
}

bootCapacityCard();
