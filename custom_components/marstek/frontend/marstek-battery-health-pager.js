// Newer/Older pages for the long lists on the battery health card (Runs and
// Restarts). A page keeps the card the same height, so cards below it stay put
// on a dashboard, where a "Show more" list would push them down with every press.
// One page shows no pager (https://design-system.service.gov.uk/components/pagination/),
// and the range ("9–16 of 42") tells how far along the list is
// (https://www.nngroup.com/articles/infinite-scrolling-tips/).

export const PAGE_SIZE = 8;

function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// The first index on ``page``, clamped, because a list can shrink under a
// stored page as old entries drop out.
export function firstOf(page, total) {
  const last = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  return Math.min(Math.max(0, Math.trunc(page) || 0), last) * PAGE_SIZE;
}

// ``name`` is the list ("runs", "restarts"). The buttons use aria-disabled, not
// disabled, so a redraw can keep focus on the one that was pressed, and they are
// described by the range, so a screen reader reads the new range with them.
export function pagerHtml(name, text, first, total) {
  if (total <= PAGE_SIZE) return "";
  const range = `${name}-range`;
  const step = (action, icon, label, off) => `<button type="button" data-action="page-${action}" aria-disabled="${off}"
    aria-describedby="${range}"><ha-icon icon="${icon}"></ha-icon>${esc(label)}</button>`;
  const shown = Math.min(PAGE_SIZE, total - first);
  return `<nav class="pager" aria-label="${esc(text.pages[name])}" data-page="${first / PAGE_SIZE}">${step("prev", "mdi:chevron-left", text.pages.prev, first === 0)}
    <span class="muted num" id="${range}">${esc(text.range(first + 1, first + shown, total))}</span>${step("next", "mdi:chevron-right", text.pages.next, first + PAGE_SIZE >= total)}</nav>`;
}
