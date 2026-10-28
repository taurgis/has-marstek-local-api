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
} satisfies Theme;
