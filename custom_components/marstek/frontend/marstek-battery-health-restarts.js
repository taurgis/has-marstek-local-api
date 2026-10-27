// The Restarts tab: device restarts and network dropouts from the Restarts sensor.
// The integration decides which is which: a dropout is silence that ends with
// the device unchanged, a restart leaves a boot trace (energy totals restored
// from the last save, the CT link lost, the mode changed, or the self-test a
// linked plug sees as the unit boots). Wi-Fi that answers a retry inside the
// same poll never reaches the sensor at all.
//
// Newest first, grouped under a heading per day that counts both kinds, as an
// event log reads best: the heading carries the date, each row its clock time
// in a <time> and one line on how long it was offline and why it counts.
// The kind is written out next to its icon, so colour never carries it alone
// (https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html). An empty
// tab says why it is empty (https://contributors.home-assistant.io/design/patterns/empty-states/).

const TRACES = ["energy_rollback", "ct_lost", "mode_changed", "boot_pulse"];
const KIND_ICON = { restart: "mdi:restart-alert", dropout: "mdi:wifi-off" };
const MONTH_MS = 30 * 86400 * 1000;
export const OUTAGES_STEP = 10;

export const RESTART_STYLES = `
  .log h3 { margin: 6px 0 0; font-size: 12px; line-height: 16px; font-weight: 500; color: var(--secondary-text-color); }
  .outages { list-style: none; margin: 0; padding: 0; }
  .outages li { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); font-size: 14px; line-height: 20px; }
  .outages li:last-child { border-bottom: 0; }
  .outages ha-icon { --mdc-icon-size: 18px; margin-top: 1px; color: var(--secondary-text-color); }
  .outages .restart ha-icon { color: var(--warning-color, #ffa600); }
  .outages .head { display: flex; justify-content: space-between; gap: 8px; }
  .outages .head strong { font-weight: 500; }
  .outages .head time { flex: none; color: var(--secondary-text-color); }
  .outages .why { display: block; margin-top: 2px; font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
  .more { align-self: center; min-height: 40px; padding: 0 16px; border-radius: 20px; font-size: 14px; color: var(--primary-color); border: 1px solid var(--divider-color); }
  .empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 0; text-align: center; }
  .empty ha-icon { --mdc-icon-size: 40px; color: var(--secondary-text-color); }
  .empty h3 { margin: 0; font-size: 14px; line-height: 20px; font-weight: 500; }
`;

function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function count(raw) {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
}

function offline(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return null;
  return value < 120 ? `${Math.round(value)} s` : `${Math.round(value / 60)} min`;
}

function localDay(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

// The sensor attributes, newest first, with a usable time on every row.
export function outagesOf(attrs) {
  const items = Array.isArray(attrs?.outages) ? attrs.outages : [];
  return items
    .filter((item) => (item?.kind === "restart" || item?.kind === "dropout") && !Number.isNaN(new Date(item.at).getTime()))
    .map((item) => ({ ...item, date: new Date(item.at) }))
    .sort((a, b) => b.date - a.date);
}

// One line: how long it was gone, then why it counts as a restart or not.
function whyOf(item, text) {
  const restart = item.kind === "restart";
  const gone = count(item.failed_polls) ? offline(item.offline_s) : null;
  const first = gone
    ? `<time datetime="PT${Math.round(item.offline_s)}S">${esc(text.offline(gone))}</time>`
    : restart ? esc(text.betweenPolls) : "";
  const traces = (Array.isArray(item.evidence) ? item.evidence : []).filter((trace) => TRACES.includes(trace));
  const second = restart ? traces.map((trace) => text.restartTrace[trace]).join(", ") : text.noTrace;
  return [first, esc(second)].filter(Boolean).join(" · ");
}

function rowHtml(item, index, shown, text, clock) {
  // The first row a "Show more" press reveals takes focus, so it can be read on.
  const focus = index === shown - OUTAGES_STEP && index > 0 ? ` tabindex="-1" data-outage="${index}"` : "";
  return `<li class="${item.kind}"${focus}><ha-icon icon="${KIND_ICON[item.kind]}"></ha-icon>
    <span><span class="head"><strong>${esc(text.outageKind[item.kind])}</strong>
      <time class="num" datetime="${esc(item.date.toISOString())}">${esc(clock(item.date))}</time></span>
      <span class="why num">${whyOf(item, text)}</span></span></li>`;
}

// The log started less than 30 days ago: say since when, not "30 days".
function sinceOf(attrs, now, day) {
  const since = new Date(attrs.tracked_since);
  return Number.isNaN(since.getTime()) || now - since >= MONTH_MS ? null : day(since);
}

// ``day`` and ``clock`` format a Date in the viewer's locale; the card passes
// its own. ``now`` is a Date, so Today and Yesterday can be tested.
export function restartsHtml(attrs, text, { day, clock, now = new Date(), tracked = true, shown = OUTAGES_STEP }) {
  if (!attrs) return `<p class="muted">${esc(tracked ? text.outagesWaiting : text.outagesOff)}</p>`;
  const since = sinceOf(attrs, now, day);
  const items = outagesOf(attrs);
  if (!items.length) {
    return `<div class="empty"><ha-icon icon="mdi:check-network-outline" aria-hidden="true"></ha-icon>
      <h3>${esc(text.noOutages(since))}</h3><p class="muted">${esc(text.outagesHelp)}</p></div>`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const names = { [localDay(now)]: text.today, [localDay(yesterday)]: text.yesterday };
  const days = new Map();
  items.forEach((item, index) => {
    const key = localDay(item.date);
    const group = days.get(key) || { title: names[key] || day(item.date), restart: 0, dropout: 0, rows: [] };
    group[item.kind] += 1;
    if (index < shown) group.rows.push(rowHtml(item, index, shown, text, clock));
    days.set(key, group);
  });
  const lists = [...days.values()].filter((group) => group.rows.length).map((group, index) => `<h3 id="outage-day-${index}">${esc(`${group.title} · ${text.dayCount(group.restart, group.dropout)}`)}</h3>
    <ul class="outages" aria-labelledby="outage-day-${index}">${group.rows.join("")}</ul>`).join("");
  const rest = items.length - shown;
  const more = rest > 0 ? `<button class="more" type="button" data-action="more-outages">${esc(text.moreOutages(Math.min(rest, OUTAGES_STEP)))}</button>` : "";
  const summary = text.outageSummary(count(attrs.restarts_30d), count(attrs.dropouts_30d), since);
  return `<p class="muted num">${esc(summary)}</p><div class="log">${lists}</div>${more}`;
}
