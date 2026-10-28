// The Corrections tab: where the BMS reset its own state of charge, from the
// usable-capacity sensor (helpers/soc_corrections.py). An LFP pack counts
// energy in and out between about 20 and 95%, where its voltage says little;
// the count drifts until the cells reach full and the BMS resets to 100%.
// A jump or a fast climb means it had counted short, a hold at 99% that it
// had counted long. The size of a reset is the drift since the last full
// charge, so the tab also says how far that drift can move a run.
//
// The sensor keeps the newest ten corrections, so the list needs no paging.
// Newest first; each row names its kind next to its icon, so colour never
// carries it alone (https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).
// An empty tab says why it is empty (https://contributors.home-assistant.io/design/patterns/empty-states/).

const STYLES = ["jump", "creep", "hold"];
const KIND_ICON = { up: "mdi:arrow-up-bold", creep: "mdi:trending-up", hold: "mdi:pause", down: "mdi:arrow-down-bold" };
const STALE_FULL_DAYS = 14;

export const CORRECTION_STYLES = `
  .fixes .hold ha-icon, .fixes .down ha-icon { color: var(--warning-color, #ffa600); }
  .fixes .why { white-space: normal; }
  .fix-help { display: flex; flex-direction: column; gap: 6px; }
`;

function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function finite(value) {
  const number = typeof value === "number" ? value : Number(value);
  return value != null && value !== "" && Number.isFinite(number) ? number : null;
}

// The sensor attribute, newest first, with a usable time and numbers on every row.
export function correctionsOf(attrs) {
  const items = Array.isArray(attrs?.soc_corrections) ? attrs.soc_corrections : [];
  return items
    .map((item) => ({ ...item, date: new Date(item?.at), from: finite(item?.from_soc), to: finite(item?.to_soc), size: finite(item?.points) }))
    .filter((item) => STYLES.includes(item.style) && !Number.isNaN(item.date.getTime()) && item.from != null && item.to != null && item.size != null)
    // A jump is the only kind that can go down: an unexplained step mid-range.
    .map((item) => ({ ...item, kind: item.style === "jump" ? (item.size < 0 ? "down" : "up") : item.style }))
    .sort((a, b) => b.date - a.date);
}

// ``fmt`` formats a number in the viewer's locale; ``when`` a Date with its time.
function rowHtml(item, text, { fmt, when }) {
  const moved = finite(item.throughput);
  const detail = [
    `${fmt(item.from, 0)}% → ${fmt(item.to, 0)}%`,
    text.fixSize(`${fmt(Math.abs(item.size), 1)} ${text.pointsWord}`),
    moved && moved > 0 ? text.fixMoved(`${fmt(moved, 0)} ${text.pointsWord}`, `${fmt((Math.abs(item.size) / moved) * 100, 1)}%`) : "",
  ].filter(Boolean).join(" · ");
  return `<li class="${item.kind}"><ha-icon icon="${KIND_ICON[item.kind]}"></ha-icon>
    <span><span class="head"><strong>${esc(text.fixKind[item.kind])}</strong>
      <time class="num" datetime="${esc(item.date.toISOString())}">${esc(when(item.date))}</time></span>
      <span class="why num">${esc(detail)}</span>
      <span class="why">${esc(text.fixWhy[item.kind])}</span></span></li>`;
}

function summaryOf(attrs, text, { fmt, day, now }) {
  const lines = [];
  const fixes = finite(attrs.soc_corrections_30d);
  const fulls = finite(attrs.full_charges_30d);
  if (fixes != null && fulls != null) lines.push(text.fixSummary(Math.round(fixes), Math.round(fulls)));
  const full = new Date(attrs.last_full_charge_at);
  if (!Number.isNaN(full.getTime())) {
    lines.push(now - full >= STALE_FULL_DAYS * 86400e3 ? text.notFull(day(full)) : text.lastFull(day(full)));
  }
  const bound = finite(attrs.soc_drift_bound_pct);
  lines.push(bound == null ? text.fixNoBound : text.fixBound(`${fmt(bound, 1)}%`));
  return lines;
}

export function correctionsHtml(attrs, text, { fmt, day, when, now = new Date() }) {
  if (!attrs || !("soc_corrections" in attrs)) return `<p class="muted">${esc(text.fixWaiting)}</p>`;
  const items = correctionsOf(attrs);
  const help = `<div class="fix-help">${text.fixHelp.map((line) => `<p class="muted">${esc(line)}</p>`).join("")}</div>`;
  const summary = summaryOf(attrs, text, { fmt, day, now }).map((line) => `<p class="muted num">${esc(line)}</p>`).join("");
  if (!items.length) {
    return `<div class="empty"><ha-icon icon="mdi:battery-check-outline" aria-hidden="true"></ha-icon>
      <h3>${esc(text.noFixes)}</h3></div>${summary}${help}`;
  }
  const rows = items.map((item) => rowHtml(item, text, { fmt, when })).join("");
  return `${summary}<ul class="outages fixes" aria-label="${esc(text.tabs.corrections)}">${rows}</ul>${help}`;
}
