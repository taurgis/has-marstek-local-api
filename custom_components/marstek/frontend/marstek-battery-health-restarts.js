// Device restarts and network dropouts from the Restarts sensor.
// The integration decides which is which: a dropout is silence that ends with
// the device unchanged, a restart leaves a boot trace (energy totals restored
// from the last save, the CT link lost, the mode changed). Wi-Fi that answers
// a retry inside the same poll never reaches the sensor at all.

const TRACES = ["energy_rollback", "ct_lost", "mode_changed"];

function count(raw) {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
}

function offline(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return null;
  return value < 120 ? `${Math.round(value)}\u00a0s` : `${Math.round(value / 60)}\u00a0min`;
}

// ``day`` formats a timestamp in the viewer's locale; the card passes its own.
export function restartInsight(attrs, text, day) {
  if (!attrs) return null;
  const restarts = count(attrs.restarts_30d);
  const dropouts = count(attrs.dropouts_30d);
  if (restarts) {
    const outages = Array.isArray(attrs.outages) ? attrs.outages : [];
    const last = outages.filter((item) => item?.kind === "restart").at(-1) || {};
    const traces = (Array.isArray(last.evidence) ? last.evidence : [])
      .filter((item) => TRACES.includes(item))
      .map((item) => text.restartTrace[item]);
    const line = text.restarts(restarts, day(attrs.last_restart_at), offline(last.offline_s), traces.join(", "));
    return { icon: "mdi:restart-alert", text: dropouts ? `${line} ${text.dropoutsToo(dropouts)}` : line };
  }
  return dropouts ? { icon: "mdi:wifi-alert", text: text.dropouts(dropouts) } : null;
}
