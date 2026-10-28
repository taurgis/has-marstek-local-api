import { defineConfig } from 'vitepress';
import type { HeadConfig } from 'vitepress';
import { pageDescriptions, pageTitles } from './pages';

// The site builds straight from docs/, so the same Markdown renders on GitHub
// and here. Pages that only make sense on the site live under guide/.
// https://vitepress.dev/reference/site-config

const siteUrl = 'https://marstek.rhino-inquisitor.com/';
const repoUrl = 'https://github.com/taurgis/has-marstek-local-api';
const siteName = 'Marstek for Home Assistant';
const siteDescription =
  'Monitor and control Marstek home batteries from Home Assistant over your local network. No cloud account, no extra hardware.';

// Docs pages link to files outside docs/ (tools/, the root README), to the
// vendor protocol note, and to the GitHub-only docs index. Those relative links
// work on GitHub but are not site pages, so send them to the repository.
function repoUrlFor(href: string, sourcePath: string): string | undefined {
  if (/^([a-z]+:|\/|#)/i.test(href)) return undefined;
  const dir = sourcePath.includes('/') ? sourcePath.slice(0, sourcePath.lastIndexOf('/') + 1) : '';
  const resolved = new URL(href, `https://repo.invalid/docs/${dir}`);
  const path = decodeURIComponent(resolved.pathname.slice(1));
  const sitePage =
    path.startsWith('docs/') &&
    !path.startsWith('docs/agents/') &&
    path !== 'docs/README.md' &&
    !path.endsWith('.MD');
  if (sitePage) return undefined;
  const kind = path.endsWith('/') ? 'tree' : 'blob';
  return `${repoUrl}/${kind}/main/${path}${resolved.hash}`;
}

function canonicalUrl(relativePath: string): string {
  const route = relativePath.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '');
  return new URL(route, siteUrl).href;
}

export default defineConfig({
  title: 'Marstek for HA',
  titleTemplate: `:title | ${siteName}`,
  description: siteDescription,
  lang: 'en-US',
  cleanUrls: true,
  lastUpdated: true,

  // Agent notes and the docs index for GitHub readers are not site pages.
  srcExclude: ['agents/**', 'README.md'],

  sitemap: { hostname: siteUrl },

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    ['meta', { name: 'theme-color', content: '#0f766e' }],
    ['meta', { property: 'og:site_name', content: siteName }],
    ['meta', { property: 'og:locale', content: 'en_US' }],
    ['link', { rel: 'apple-touch-icon', href: '/apple-touch-icon.png' }],
    // One share card for every page: https://ogp.me/
    ['meta', { property: 'og:image', content: `${siteUrl}og.png` }],
    ['meta', { property: 'og:image:width', content: '1200' }],
    ['meta', { property: 'og:image:height', content: '630' }],
    ['meta', { property: 'og:image:alt', content: `${siteName}: your home battery, on your own network.` }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
  ],

  markdown: {
    config(md) {
      // Raw <img src="screenshots/x.png"> is relative on GitHub, but Vue reads
      // a bare path as a package import. Make it explicitly relative.
      md.core.ruler.push('relative_html_img', (state) => {
        const fix = (html: string) =>
          html.replace(/(<img\b[^>]*?\ssrc=")(?![a-z]+:|\/|\.)/gi, '$1./');
        for (const token of state.tokens) {
          if (token.type === 'html_block') token.content = fix(token.content);
          for (const child of token.children ?? []) {
            if (child.type === 'html_inline') child.content = fix(child.content);
          }
        }
      });

      const defaultOpen =
        md.renderer.rules.link_open ??
        ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
      md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
        const href = tokens[idx].attrGet('href');
        const repoHref = href ? repoUrlFor(href, env.relativePath ?? '') : undefined;
        if (repoHref) {
          tokens[idx].attrSet('href', repoHref);
          tokens[idx].attrSet('target', '_blank');
          tokens[idx].attrSet('rel', 'noreferrer');
        }
        return defaultOpen(tokens, idx, options, env, self);
      };
    },
  },

  transformPageData(pageData) {
    const description = pageData.frontmatter.description ?? pageDescriptions[pageData.relativePath];
    if (description) pageData.description = description;
    const shortTitle = pageTitles[pageData.relativePath];
    if (shortTitle) pageData.title = shortTitle;
    const url = canonicalUrl(pageData.relativePath);
    const title = pageData.frontmatter.layout === 'home' ? siteName : `${pageData.title} | ${siteName}`;
    const head: HeadConfig[] = [
      ['link', { rel: 'canonical', href: url }],
      ['meta', { property: 'og:type', content: 'website' }],
      ['meta', { property: 'og:url', content: url }],
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: pageData.description || siteDescription }],
    ];
    pageData.frontmatter.head = [...(pageData.frontmatter.head ?? []), ...head];
  },

  themeConfig: {
    logo: { src: '/favicon.svg', alt: '' },
    siteTitle: 'Marstek for HA',

    nav: [
      {
        text: 'Get started',
        link: '/guide/getting-started',
        activeMatch: '^/guide/(introduction|supported-devices|before-you-start|getting-started|features|limitations)',
      },
      { text: 'Use it', link: '/services', activeMatch: '^/(services|energy_dashboard|usable-capacity|screenshots)' },
      {
        text: 'Reference',
        link: '/entities',
        activeMatch: '^/(entities|repairs|installation|configuration|options|guide/glossary|capacity-estimation-methods|development)',
      },
      { text: 'Compare', link: '/guide/comparison', activeMatch: '^/guide/comparison' },
      { text: 'Help', link: '/troubleshooting', activeMatch: '^/(troubleshooting|firmware-resets|guide/faq)' },
    ],

    sidebar: [
      {
        text: 'Start here',
        items: [
          { text: 'What is this?', link: '/guide/introduction' },
          { text: 'Will it work for me?', link: '/guide/supported-devices' },
          { text: 'Before you start', link: '/guide/before-you-start' },
          { text: 'Getting started', link: '/guide/getting-started' },
        ],
      },
      {
        text: 'Decide',
        items: [
          { text: 'Features', link: '/guide/features' },
          { text: 'Limitations', link: '/guide/limitations' },
          { text: 'Compared with other integrations', link: '/guide/comparison' },
        ],
      },
      {
        text: 'Use it',
        items: [
          { text: 'Visual tour', link: '/screenshots' },
          { text: 'Energy dashboard', link: '/energy_dashboard' },
          { text: 'Automations and services', link: '/services' },
          { text: 'Battery health', link: '/usable-capacity' },
        ],
      },
      {
        text: 'Reference',
        items: [
          { text: 'Installation', link: '/installation' },
          { text: 'Configuration', link: '/configuration' },
          { text: 'Options', link: '/options' },
          { text: 'Entities', link: '/entities' },
          { text: 'Repairs', link: '/repairs' },
          { text: 'Glossary', link: '/guide/glossary' },
        ],
      },
      {
        text: 'Help',
        items: [
          { text: 'FAQ', link: '/guide/faq' },
          { text: 'Troubleshooting', link: '/troubleshooting' },
          { text: 'Why a battery goes offline', link: '/firmware-resets' },
        ],
      },
      {
        text: 'Under the hood',
        collapsed: true,
        items: [
          { text: 'How capacity estimation works', link: '/capacity-estimation-methods' },
          { text: 'Development', link: '/development' },
          { text: 'Open API protocol', link: `${repoUrl}/blob/main/docs/marstek_device_openapi.MD` },
        ],
      },
    ],

    socialLinks: [{ icon: 'github', link: repoUrl }],

    // https://vitepress.dev/reference/default-theme-search
    search: { provider: 'local' },

    editLink: {
      pattern: `${repoUrl}/edit/main/docs/:path`,
      text: 'Suggest a change to this page',
    },

    lastUpdated: { text: 'Last updated' },

    outline: { level: [2, 3], label: 'On this page' },

    footer: {
      message:
        'MIT licensed community project. Not affiliated with or endorsed by Marstek. Marstek is a trademark of its owner.',
      copyright: `<a href="${repoUrl}">taurgis/has-marstek-local-api</a>`,
    },
  },
});
