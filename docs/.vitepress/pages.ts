// Search-result and link-preview descriptions for pages that also render on
// GitHub, where front matter would show up as a table. Pages written only for
// the site (guide/, index.md) set `description` in their own front matter.
export const pageDescriptions: Record<string, string> = {
  'installation.md': 'Install the Marstek integration with HACS or by hand, and the requirements to check first.',
  'configuration.md': 'Add a Marstek battery in Home Assistant, by discovery or by IP address, and what happens when its IP or firmware changes.',
  'screenshots.md': 'A screenshot walk-through of setup, the Battery health dashboard, the cards, automations, the Energy dashboard, and Repairs.',
  'energy_dashboard.md': 'Which Marstek sensors to add to the Home Assistant Energy dashboard, and why they report watt-hours.',
  'services.md': 'Charge, discharge, passive mode, and manual schedules from Home Assistant automations, with copy-paste YAML.',
  'usable-capacity.md': 'Estimate usable capacity and state of health for a Marstek battery, with an optional dashboard, cards, and a linked smart plug.',
  'options.md': 'Polling intervals, network timeouts, power limits, the Battery health dashboard, and the optional energy meter.',
  'entities.md': 'Every sensor, switch, number, and select the Marstek integration creates, including the ones that start disabled.',
  'repairs.md': 'The connection repair and firmware warnings the Marstek integration can raise in Settings > Repairs.',
  'troubleshooting.md': 'Fix discovery problems, unavailable entities, missing controls, odd energy totals, and Venus C Auto-mode stalls.',
  'firmware-resets.md': 'Why a Marstek battery drops off the network, restarts, or switches its Local API off, in plain language.',
  'capacity-estimation-methods.md': 'How the integration estimates usable battery capacity, and why it is built that way.',
  'development.md': 'Run the tests, the firmware emulator matrix, and the release flow for the Marstek integration.',
};

// Shorter <title> values for pages whose H1 is long or written for GitHub.
export const pageTitles: Record<string, string> = {
  'capacity-estimation-methods.md': 'How capacity estimation works',
};
