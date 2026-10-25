// Live watts of an energy meter linked in the integration's options.
// The usable-capacity sensor names the linked energy sensors; the power
// sensor on the same device (a HomeWizard Energy Socket, a Shelly plug) is
// what the cards show. Packs without a linked meter cost nothing here.

// hass.entities is replaced, not mutated, when the registry changes, so a
// found power sensor is kept until then. A miss is not kept: the plug's
// states can arrive after the registry.
const found = new WeakMap();

export function plugPowerId(hass, attrs) {
  const meter = attrs?.plug_energy_entities;
  const energyId = Array.isArray(meter) ? meter[0] : null;
  const entities = hass?.entities;
  if (typeof energyId !== "string" || !entities) return null;
  let byMeter = found.get(entities);
  if (!byMeter) found.set(entities, (byMeter = new Map()));
  if (!byMeter.get(energyId)) byMeter.set(energyId, scan(hass, energyId));
  return byMeter.get(energyId);
}

function scan(hass, energyId) {
  const device = hass.entities[energyId]?.device_id;
  if (!device) return null;
  const powers = Object.values(hass.entities).filter((entity) => entity.device_id === device
    && !entity.disabled_by
    && entity.entity_id.startsWith("sensor.")
    && hass.states[entity.entity_id]?.attributes?.device_class === "power");
  // A per-phase sensor (HomeWizard's "Power phase 1") repeats the total under a longer id.
  powers.sort((a, b) => a.entity_id.length - b.entity_id.length || a.entity_id.localeCompare(b.entity_id));
  return powers[0]?.entity_id ?? null;
}

// Plugs count import (into the battery) as positive watts.
export function plugFlow(watts) {
  if (watts == null || Math.abs(watts) < 1) return "idle";
  return watts > 0 ? "in" : "out";
}
