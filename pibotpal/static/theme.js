// SPDX-License-Identifier: MIT
// Apply the stored choice before first paint; the menu binds once the DOM exists.
(() => {
  const key = 'pibotpal.appearance';
  const choices = ['auto', 'light', 'dark'];
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let choice = 'auto';
  try { const stored = localStorage.getItem(key); if (choices.includes(stored)) choice = stored; } catch {}
  const icons = {
    auto: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    dark: '<path d="M20 14a8.4 8.4 0 0 1-10-10A8.5 8.5 0 1 0 20 14Z"/>',
  };
  function apply() {
    const theme = choice === 'auto' ? (media.matches ? 'dark' : 'light') : choice;
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.appearance = choice;
    document.documentElement.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0c1217' : '#f3f6f7');
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
      const label = 'Appearance: ' + choice[0].toUpperCase() + choice.slice(1);
      toggle.setAttribute('aria-label', label); toggle.title = label;
      toggle.querySelector('svg').innerHTML = icons[choice];
      document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-checked', String(button.dataset.themeChoice === choice)));
    }
  }
  apply();
  media.addEventListener('change', () => { if (choice === 'auto') apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    choice = choices.includes(event.newValue) ? event.newValue : 'auto'; apply();
  });
  document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('theme-toggle');
    const menu = document.getElementById('theme-menu');
    const buttons = [...menu.querySelectorAll('[data-theme-choice]')];
    function close(focus = false) { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); if (focus) toggle.focus(); }
    function open() { menu.hidden = false; toggle.setAttribute('aria-expanded', 'true'); buttons.find(button => button.dataset.themeChoice === choice).focus(); }
    toggle.addEventListener('click', () => menu.hidden ? open() : close(true));
    toggle.addEventListener('keydown', event => { if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); open(); } });
    buttons.forEach(button => button.addEventListener('click', () => {
      choice = button.dataset.themeChoice;
      try { localStorage.setItem(key, choice); } catch {}
      apply(); close(true);
    }));
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return; }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const index = buttons.indexOf(document.activeElement);
      buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length].focus();
    });
    document.addEventListener('pointerdown', event => { if (!event.target.closest('.theme-picker')) close(); });
    document.addEventListener('focusin', event => { if (!event.target.closest('.theme-picker')) close(); });
    apply();
  });
})();
