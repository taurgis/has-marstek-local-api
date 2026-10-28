// The Corrections tab: where the BMS reset its own state of charge, from the
// usable-capacity sensor (helpers/soc_corrections.py). An LFP pack counts
// energy in and out between about 20 and 95%, where its voltage says little;
// the count drifts until the cells reach full and the BMS resets to 100%.
// A jump or a fast climb means it had counted short, a hold at 99% that it
// had counted long. The size of a reset is the drift since the last full
// charge, so the tab also says how far that drift can move a run.
//
// The sensor keeps the newest ten corrections, so the list needs no paging.
// Rows follow the Home Assistant list pattern: a short first line and one
// line of detail (https://contributors.home-assistant.io/design/components/list/).
// What each kind means sits once behind a disclosure, not on every row
// (https://www.nngroup.com/articles/progressive-disclosure/); <details> is
// the native disclosure (https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/).
// Each row names its kind next to its icon, so colour never carries it alone
// (https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).
//
// An arrival at 100% stores ``from_soc`` as the start of the stretch it was
// judged over, 15 points below full, not where the jump began. Only a step
// mid-range shows its two ends.

const STYLES = ["jump", "creep", "hold"];
const KINDS = ["up", "creep", "hold", "down"];
const KIND_ICON = { up: "mdi:arrow-up-bold", creep: "mdi:trending-up", hold: "mdi:pause", down: "mdi:arrow-down-bold" };
const STALE_FULL_DAYS = 14;
// Bars share one scale with room for a modest correction, so three small
// ones do not fill the strip.
const MIN_SCALE_POINTS = 5;

export const CORRECTION_STYLES = `
  .fixes .hold ha-icon, .fixes .down ha-icon { color: var(--warning-color, #ffa600); }
  .fixes .size { flex: none; font-weight: 500; }
  .strip { display: flex; flex-direction: column; gap: 4px; }
  .strip .bars { display: flex; align-items: stretch; gap: 4px; height: 64px; border-bottom: 0; position: relative; }
  .strip .bars::after { content: ""; position: absolute; left: 0; right: 0; top: 50%; border-top: 1px solid var(--divider-color); }
  .strip .slot { flex: 1; max-width: 28px; display: flex; flex-direction: column; }
  .strip .half { flex: 1; display: flex; }
  .strip .half.top { align-items: flex-end; }
  .strip .half.bottom { align-items: flex-start; }
  .strip i { display: block; width: 100%; min-height: 2px; border-radius: 3px; background: var(--primary-color); }
  .strip .hold i, .strip .down i { background: var(--warning-color, #ffa600); }
  .strip .ends { display: flex; justify-content: space-between; gap: 8px; white-space: nowrap; }
  details.fix-help summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; gap: 6px; font-size: 14px; color: var(--primary-color); list-style: none; }
  details.fix-help summary::-webkit-details-marker { display: none; }
  details.fix-help summary ha-icon { --mdc-icon-size: 18px; transition: transform 0.15s; }
  details.fix-help[open] summary ha-icon { transform: rotate(90deg); }
  details.fix-help dl { margin: 0 0 8px; display: grid; gap: 6px; font-size: 12px; line-height: 16px; }
  details.fix-help dt { font-weight: 500; color: var(--primary-text-color); }
  details.fix-help dd { margin: 0; color: var(--secondary-text-color); }
  details.fix-help p + p { margin-top: 6px; }
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

const signed = (fmt, value) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${fmt(Math.abs(value), 1)}`;

// ``fmt`` formats a number in the viewer's locale; ``when`` a Date with its time.
function rowHtml(item, text, { fmt, when }) {
  const moved = finite(item.throughput);
  const detail = [
    item.to < 100 ? `${fmt(item.from, 0)}% → ${fmt(item.to, 0)}%` : "",
    moved && moved > 0 ? text.fixShare(`${fmt((Math.abs(item.size) / moved) * 100, 1)}%`, fmt(moved, 0)) : "",
  ].filter(Boolean).join(" · ");
  const time = `<time datetime="${esc(item.date.toISOString())}">${esc(when(item.date))}</time>`;
  return `<li class="${item.kind}"><ha-icon icon="${KIND_ICON[item.kind]}"></ha-icon>
    <span><span class="head"><strong>${esc(text.fixKind[item.kind])}</strong>
      <span class="size num">${esc(`${signed(fmt, item.size)} ${text.pointsWord}`)}</span></span>
      <span class="why num">${[time, esc(detail)].filter(Boolean).join(" · ")}</span></span></li>`;
}

// Oldest left, newest right: up for a pack that counted short, down for one
// that counted long. The list below carries every value, so the strip is one
// image with a summary for screen readers.
function stripHtml(items, text, { fmt, day }) {
  if (items.length < 2) return "";
  const ordered = [...items].reverse();
  const scale = Math.max(MIN_SCALE_POINTS, ...ordered.map((item) => Math.abs(item.size)));
  const sizes = ordered.map((item) => Math.abs(item.size));
  const label = text.fixChart(ordered.length, day(ordered[0].date), day(ordered.at(-1).date), fmt(Math.min(...sizes), 1), fmt(Math.max(...sizes), 1));
  const bars = ordered.map((item) => {
    const bar = `<i style="height:${((Math.abs(item.size) / scale) * 100).toFixed(1)}%"></i>`;
    const up = item.size > 0;
    return `<span class="slot ${item.kind}"><span class="half top">${up ? bar : ""}</span><span class="half bottom">${up ? "" : bar}</span></span>`;
  }).join("");
  return `<div class="strip"><div class="bars" role="img" aria-label="${esc(label)}">${bars}</div>
    <div class="ends muted num" aria-hidden="true"><span>${esc(day(ordered[0].date))}</span><span>${esc(day(ordered.at(-1).date))}</span></div>
    <p class="muted" aria-hidden="true">${esc(text.fixAxis)}</p></div>`;
}

function summaryOf(attrs, text, { fmt, day, now }) {
  const counts = [];
  const fixes = finite(attrs.soc_corrections_30d);
  const fulls = finite(attrs.full_charges_30d);
  if (fixes != null && fulls != null) counts.push(text.fixSummary(Math.round(fixes), Math.round(fulls)));
  const full = new Date(attrs.last_full_charge_at);
  const stale = !Number.isNaN(full.getTime()) && now - full >= STALE_FULL_DAYS * 86400e3;
  if (!Number.isNaN(full.getTime()) && !stale) counts.push(text.lastFull(day(full)));
  const lines = [counts.join(" · ")];
  if (stale) lines.push(text.notFull(day(full)));
  const bound = finite(attrs.soc_drift_bound_pct);
  if (bound != null) lines.push(text.fixBound(`${fmt(bound, 1)}%`, bound >= 5 ? "low" : bound >= 2 ? "medium" : null));
  return lines.filter(Boolean);
}

function helpHtml(text) {
  const kinds = KINDS.map((kind) => `<dt>${esc(text.fixKind[kind])}</dt><dd>${esc(text.fixWhy[kind])}</dd>`).join("");
  return `<details class="fix-help"><summary><ha-icon icon="mdi:chevron-right" aria-hidden="true"></ha-icon>${esc(text.fixLearn)}</summary>
    <dl>${kinds}</dl>${text.fixHelp.map((line) => `<p class="muted">${esc(line)}</p>`).join("")}</details>`;
}

export function correctionsHtml(attrs, text, { fmt, day, when, now = new Date() }) {
  if (!attrs || !("soc_corrections" in attrs)) return `<p class="muted">${esc(text.fixWaiting)}</p>`;
  const items = correctionsOf(attrs);
  const summary = summaryOf(attrs, text, { fmt, day, now }).map((line) => `<p class="muted num">${esc(line)}</p>`).join("");
  if (!items.length) {
    return `<div class="empty"><ha-icon icon="mdi:battery-check-outline" aria-hidden="true"></ha-icon>
      <h3>${esc(text.noFixes)}</h3><p class="muted">${esc(text.noFixesHelp)}</p></div>${summary}${helpHtml(text)}`;
  }
  const rows = items.map((item) => rowHtml(item, text, { fmt, when })).join("");
  return `${summary}${stripHtml(items, text, { fmt, day })}<ul class="outages fixes" aria-label="${esc(text.tabs.corrections)}">${rows}</ul>${helpHtml(text)}`;
}
