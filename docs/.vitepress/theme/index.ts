import type { Theme } from 'vitepress';
import DefaultTheme from 'vitepress/theme';
import { h } from 'vue';
import LocalFlowHero from './components/LocalFlowHero.vue';
import './custom.css';

// The home page hero image slot shows how the integration talks to the
// battery: straight over the LAN, with the cloud left out.
// https://vitepress.dev/guide/extending-default-theme#layout-slots
export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      'home-hero-image': () => h(LocalFlowHero),
    });
  },
  enhanceApp() {
    if (typeof window === 'undefined') return;
    // The default theme's phone menu ignores Escape; close it and hand
    // focus back to the button that opened it.
    window.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !document.querySelector('.VPNavScreen')) return;
      const toggle = document.querySelector<HTMLButtonElement>('.VPNavBarHamburger');
      toggle?.click();
      toggle?.focus();
    });
    // Desktop screenshots shrink to a few pixels of text on a phone; a tap
    // opens the image at full size, where the browser can zoom it.
    document.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLImageElement) || target.closest('a')) return;
      if (!target.closest('.vp-doc')) return;
      window.open(target.src, '_blank', 'noopener');
    });
  },
} satisfies Theme;
