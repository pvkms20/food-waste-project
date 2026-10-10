// Sidebar behaviour and small shared helpers. No innerHTML: all text is set with textContent.
(function () {
  const root = document.documentElement;
  const toggle = document.getElementById('sb-toggle');
  const open = document.getElementById('nav-open');
  const scrim = document.getElementById('scrim');
  const set = (collapsed) => {
    root.classList.toggle('sb-collapsed', collapsed);
    if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
    try { localStorage.setItem('ft-sb', collapsed ? '1' : '0'); } catch (e) { /* storage blocked: ignore */ }
  };
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(!root.classList.contains('sb-collapsed')));
    toggle.addEventListener('click', () => set(!root.classList.contains('sb-collapsed')));
  }
  const setNav = (isOpen) => { root.classList.toggle('nav-open', isOpen); };
  if (open) open.addEventListener('click', () => setNav(true));
  if (scrim) scrim.addEventListener('click', () => setNav(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setNav(false); });
  document.querySelectorAll('[data-dismiss]').forEach((b) => b.addEventListener('click', () => b.parentElement.remove()));
  // Buttons that submit a form show a busy state and cannot be double-clicked.
  document.querySelectorAll('form[data-once]').forEach((f) => f.addEventListener('submit', (e) => {
    const b = f.querySelector('button[type=submit], button:not([type])');
    if (f.dataset.sent) { e.preventDefault(); return; }
    f.dataset.sent = '1';
    if (b) b.setAttribute('aria-busy', 'true');
  }));
  document.querySelectorAll('form[data-confirm]').forEach((f) => f.addEventListener('submit', (e) => {
    if (!window.confirm(f.dataset.confirm)) {
      e.preventDefault(); delete f.dataset.sent;
      const b = f.querySelector('button'); if (b) b.removeAttribute('aria-busy');
    }
  }));
})();
