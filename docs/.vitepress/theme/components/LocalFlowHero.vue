<!--
  Static SVG, so it renders the same during SSR. The moving dots are SMIL and
  are hidden under prefers-reduced-motion.
-->

<template>
  <figure class="mk-hero">
    <svg
      viewBox="0 0 420 300"
      role="img"
      aria-labelledby="mk-hero-title mk-hero-desc"
      xmlns="http://www.w3.org/2000/svg"
    >
      <title id="mk-hero-title">Home Assistant talks to the battery over your home network</title>
      <desc id="mk-hero-desc">
        Home Assistant and a Marstek battery exchange messages directly on the local network.
        The Marstek cloud is not on that path.
      </desc>

      <!-- Cloud, crossed out -->
      <g class="mk-cloud">
        <path
          d="M168 70a22 22 0 0 1 42-10 18 18 0 0 1 30 14 16 16 0 0 1-2 32h-66a18 18 0 0 1-4-36z"
        />
        <line x1="160" y1="40" x2="252" y2="112" class="mk-strike" />
        <text x="210" y="134" text-anchor="middle">no cloud needed</text>
      </g>

      <!-- Home Assistant -->
      <g transform="translate(36 170)">
        <rect width="104" height="96" rx="14" class="mk-card" />
        <path d="M52 20 22 46v30h60V46z" class="mk-ha" />
        <circle cx="52" cy="54" r="6" class="mk-ha-dot" />
        <text x="52" y="116" text-anchor="middle" class="mk-label">Home Assistant</text>
      </g>

      <!-- Battery -->
      <g transform="translate(284 156)">
        <rect x="34" y="0" width="36" height="10" rx="3" class="mk-battery-shell" />
        <rect x="14" y="8" width="76" height="102" rx="12" class="mk-battery-shell" />
        <rect x="24" y="52" width="56" height="48" rx="6" class="mk-battery-charge" />
        <path d="M58 22 44 50h12l-6 22 18-30H56z" class="mk-bolt" />
        <text x="52" y="130" text-anchor="middle" class="mk-label">Marstek battery</text>
      </g>

      <!-- LAN link -->
      <path d="M144 218 H294" class="mk-link" />
      <path d="M144 230 H294" class="mk-link mk-link-back" />
      <text x="219" y="204" text-anchor="middle" class="mk-small">local network · UDP</text>
      <circle r="5" class="mk-packet">
        <animateMotion dur="2.4s" repeatCount="indefinite" path="M144 218 H294" />
      </circle>
      <circle r="5" class="mk-packet mk-packet-back">
        <animateMotion dur="2.4s" begin="-1.2s" repeatCount="indefinite" path="M294 230 H144" />
      </circle>
    </svg>
  </figure>
</template>

<style scoped>
.mk-hero {
  margin: 0 auto;
  width: min(100%, 420px);
}

svg {
  width: 100%;
  height: auto;
  font-family: var(--vp-font-family-base);
}

.mk-card {
  fill: var(--mk-surface);
  stroke: var(--vp-c-divider);
  stroke-width: 2;
}

.mk-ha {
  fill: none;
  stroke: #18bcf2;
  stroke-width: 6;
  stroke-linejoin: round;
}

.mk-ha-dot {
  fill: #18bcf2;
}

.mk-battery-shell {
  fill: var(--mk-surface);
  stroke: var(--vp-c-brand-1);
  stroke-width: 4;
}

.mk-battery-charge {
  fill: var(--vp-c-brand-1);
  opacity: 0.85;
}

.mk-bolt {
  fill: var(--mk-charge);
}

.mk-label {
  fill: var(--vp-c-text-1);
  font-size: 14px;
  font-weight: 600;
}

.mk-small {
  fill: var(--vp-c-text-2);
  font-size: 12px;
}

.mk-link {
  stroke: var(--vp-c-brand-1);
  stroke-width: 3;
  stroke-dasharray: 6 6;
  fill: none;
}

.mk-link-back {
  stroke: var(--mk-charge);
  opacity: 0.6;
}

.mk-packet {
  fill: var(--vp-c-brand-1);
}

.mk-packet-back {
  fill: var(--mk-charge);
}

.mk-cloud path {
  fill: none;
  stroke: var(--mk-line);
  stroke-width: 3;
}

.mk-cloud text {
  fill: var(--vp-c-text-2);
  font-size: 12px;
}

.mk-strike {
  stroke: #dc2626;
  stroke-width: 4;
  stroke-linecap: round;
}

@media (prefers-reduced-motion: reduce) {
  .mk-packet {
    display: none;
  }
}
</style>
