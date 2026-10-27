// The Restarts tab: device restarts and network dropouts from the Restarts sensor.
// The integration decides which is which: a dropout is silence that ends with
// the device unchanged, a restart leaves a boot trace (energy totals restored
// from the last save, the CT link lost, the mode changed, or the self-test a
// linked plug sees as the unit boots). Wi-Fi that answers a retry inside the
// same poll never reaches the sensor at all.
//
// Newest first, grouped under a heading per day, as an event log reads best:
// the day heading carries the date, each row its clock time in a <time>.
// The kind is written out next to its icon, so colour never carries it alone
// (https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html). An empty
// tab says why it is empty (https://contributors.home-assistant.io/design/patterns/empty-states/).

const TRACES = ["energy_rollback", "ct_lost", "mode_changed", "boot_pulse"];
const KIND_ICON = { restart: "mdi:restart-alert", dropout: "mdi:wifi-off" };
export const OUTAGES_STEP = 10;

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

// ISO 8601 duration for <time datetime>, in whole seconds.
const isoDuration = (seconds) => `PT${Math.round(seconds)}S`;

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

function detailOf(item, text) {
  const failed = count(item.failed_polls);
  const parts = [];
  const gone = offline(item.offline_s);
  if (failed && gone) parts.push(`<time datetime="${isoDuration(item.offline_s)}">${esc(text.offlineFor(gone))}</time>`);
  parts.push(esc(failed ? text.failedPolls(failed) : text.betweenPolls));
  return parts.join(" · ");
}

function rowHtml(item, index, shown, text, clock) {
  const traces = (Array.isArray(item.evidence) ? item.evidence : []).filter((trace) => TRACES.includes(trace));
  const named = traces.map((trace) => text.restartTrace[trace]).join(", ");
  // The trace words sit mid-sentence elsewhere; on their own line they start with a capital.
  const why = item.kind === "restart" ? named.charAt(0).toLocaleUpperCase() + named.slice(1) : text.noTrace;
  // The first row a "Show more" press reveals takes focus, so it can be read on.
  const focus = index === shown - OUTAGES_STEP && index > 0 ? ` tabindex="-1" data-outage="${index}"` : "";
  return `<li class="${item.kind}"${focus}><ha-icon icon="${KIND_ICON[item.kind]}"></ha-icon>
    <span class="what"><span class="head"><strong>${esc(text.outageKind[item.kind])}</strong>
      <time class="num" datetime="${esc(item.date.toISOString())}">${esc(clock(item.date))}</time></span>
      <span class="num">${detailOf(item, text)}</span>${why ? `<span>${esc(why)}</span>` : ""}</span></li>`;
}

// ``fmt.day`` and ``fmt.clock`` format a Date in the viewer's locale; the card
// passes its own. ``now`` is a Date, so Today and Yesterday can be tested.
export function restartsHtml(attrs, text, { day, clock, now = new Date(), tracked = true, shown = OUTAGES_STEP }) {
  if (!attrs) return `<p class="muted">${esc(tracked ? text.outagesWaiting : text.outagesOff)}</p>`;
  const restarts = count(attrs.restarts_30d);
  const dropouts = count(attrs.dropouts_30d);
  const items = outagesOf(attrs);
  if (!items.length) {
    return `<div class="empty"><ha-icon icon="mdi:check-network-outline" aria-hidden="true"></ha-icon>
      <h3>${esc(text.noOutages)}</h3><p class="muted">${esc(text.outagesHelp)}</p></div>`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const names = { [localDay(now)]: text.today, [localDay(yesterday)]: text.yesterday };
  const groups = [];
  items.slice(0, shown).forEach((item, index) => {
    const key = localDay(item.date);
    if (groups.at(-1)?.key !== key) groups.push({ key, title: names[key] || day(item.date), rows: [] });
    groups.at(-1).rows.push(rowHtml(item, index, shown, text, clock));
  });
  const lists = groups.map((group, index) => `<h3 id="outage-day-${index}">${esc(group.title)}</h3>
    <ul class="outages" aria-labelledby="outage-day-${index}">${group.rows.join("")}</ul>`).join("");
  const rest = items.length - shown;
  const more = rest > 0 ? `<button class="more" type="button" data-action="more-outages">${esc(text.moreOutages(Math.min(rest, OUTAGES_STEP)))}</button>` : "";
  return `<p class="muted num">${esc(text.outageSummary(restarts, dropouts))}</p><div class="log">${lists}</div>${more}`;
}
