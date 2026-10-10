// Prediction page: validation, request, animated result. Server text is inserted with textContent
// only (never innerHTML), so user-provided content cannot inject markup.
(function () {
  const $ = (id) => document.getElementById(id);
  const form = $('predict-form');
  if (!form) return;
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const FIELDS = {
    meals_served: ['e-meals', 'Meals served', 1, 100000], food_prepared_kg: ['e-prep', 'Food prepared', 0.1, 100000],
    kitchen_staff: ['e-staff', 'Kitchen staff', 1, 1000], temperature: ['e-temp', 'Temperature', -30, 60],
    humidity: ['e-hum', 'Humidity', 0, 100], previous_waste_kg: ['e-prev', 'Previous waste', 0, 100000],
    edible_percent: ['e-edible', 'Share still safe', 0, 100], hours_to_expiry: ['e-hours', 'Shelf life', 0, 720],
  };
  const DECISIONS = {
    DONATE: ['donate', 'Donate the edible surplus', 'check'],
    DISPOSE: ['dispose', 'Too little to donate: dispose responsibly', 'alert'],
    DO_NOT_DONATE: ['stop', 'Do not donate this food', 'stop'],
  };
  const ICONS = {
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
    stop: '<circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/>',
  };

  function setError(name, message) {
    const [errId] = FIELDS[name];
    const input = $(name);
    $(errId).textContent = message || '';
    if (message) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
  }

  function validate() {
    let first = null;
    for (const [name, [, label, min, max]] of Object.entries(FIELDS)) {
      const el = $(name);
      let msg = '';
      const raw = el.value.trim();
      if (raw === '' && name !== 'edible_percent' && name !== 'hours_to_expiry') msg = label + ' is required.';
      else if (raw !== '') {
        const n = Number(raw);
        if (!Number.isFinite(n)) msg = label + ' must be a number.';
        else if (n < min || n > max) msg = label + ' must be between ' + min + ' and ' + max + '.';
      }
      setError(name, msg);
      if (msg && !first) first = el;
    }
    const meals = Number($('meals_served').value), food = Number($('food_prepared_kg').value);
    if (!first && meals > 0 && food > 0) {
      const per = food / meals;
      if (per < 0.02 || per > 5) {
        setError('food_prepared_kg', 'That is ' + per.toFixed(3) + ' kg per meal, which is not plausible. Check meals and food prepared.');
        first = $('food_prepared_kg');
      }
    }
    if (first) first.focus();
    return !first;
  }

  function formError(message) {
    const box = $('form-error');
    box.querySelector('span').textContent = message || '';
    box.hidden = !message;
  }

  function countUp(el, target) {
    const text = target.toFixed(1);
    if (reduce) { el.textContent = text; return; }
    const start = performance.now(), dur = 650;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - t, 3);
      el.textContent = (target * eased).toFixed(1);
      if (t < 1) requestAnimationFrame(tick); else el.textContent = text;
    };
    requestAnimationFrame(tick);
  }

  function render(r, inputs) {
    const result = $('result');
    const [cls, title, icon] = DECISIONS[r.decision] || ['dispose', r.decision, 'alert'];
    $('decision').className = 'decision ' + cls;
    const mark = $('decision-mark');
    while (mark.firstChild) mark.removeChild(mark.firstChild);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon'); svg.setAttribute('viewBox', '0 0 24 24');
    const holder = new DOMParser().parseFromString('<svg xmlns="http://www.w3.org/2000/svg">' + ICONS[icon] + '</svg>', 'image/svg+xml');
    Array.from(holder.documentElement.childNodes).forEach((n) => svg.appendChild(document.importNode(n, true)));
    mark.appendChild(svg);
    $('decision-title').textContent = title;
    $('decision-reason').textContent = r.reason + (r.warning ? ' ' + r.warning : '');
    $('result-lead').textContent = 'Based on ' + inputs.meals_served + ' meals and ' + inputs.food_prepared_kg + ' kg prepared. Model: ' + r.model_name + '.';

    const warn = $('input-warnings');
    warn.hidden = !(r.input_warnings && r.input_warnings.length);
    warn.querySelector('span').textContent = (r.input_warnings || []).join(' ');

    countUp($('q-waste'), r.waste_kg);
    countUp($('q-edible'), r.edible_kg);
    countUp($('q-offer'), r.donate_kg);
    const bar = $('range-bar');
    if (r.interval) {
      const [lo, hi] = r.interval, max = Math.max(hi * 1.15, r.waste_kg * 1.15, 1);
      bar.hidden = false;
      $('range-band').style.left = (lo / max * 100) + '%';
      $('range-band').style.width = ((hi - lo) / max * 100) + '%';
      $('range-dot').style.left = (r.waste_kg / max * 100) + '%';
      $('q-waste-note').textContent = Math.round((r.interval_level || 0.9) * 100) + '% range: ' + lo.toFixed(1) + ' to ' + hi.toFixed(1) + ' kg';
    } else { bar.hidden = true; $('q-waste-note').textContent = ''; }
    $('q-edible-note').textContent = r.edible_percent + '% of predicted waste judged safe to donate';
    $('q-offer-note').textContent = r.decision === 'DONATE'
      ? 'For ' + r.suggested_ngo + ' (takes up to ' + r.ngo_capacity + ' kg)' : 'Nothing to offer';

    const risk = $('risk-badge');
    risk.textContent = 'Waste risk: ' + r.risk.charAt(0) + r.risk.slice(1).toLowerCase();
    risk.className = 'badge ' + ({ LOW: 'ok', MEDIUM: 'warn', HIGH: 'bad' }[r.risk] || '');

    const link = $('donate-link');
    link.hidden = r.decision !== 'DONATE';
    if (!link.hidden) link.href = '/donate?qty=' + encodeURIComponent(r.donate_kg);

    result.hidden = false;
    result.classList.remove('enter'); void result.offsetWidth; result.classList.add('enter');
    result.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    result.focus({ preventScroll: true });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    formError('');
    if (!validate()) return;
    const button = $('predict-btn');
    if (button.getAttribute('aria-busy') === 'true') return;
    button.setAttribute('aria-busy', 'true'); button.disabled = true;
    $('predict-btn-text').textContent = 'Predicting';
    try {
      const body = Object.fromEntries(new FormData(form).entries());
      body.special_event = $('special_event').checked ? 1 : 0;
      const response = await fetch('/predict', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({ error: 'Unexpected response from the server.' }));
      if (!response.ok) { formError(result.error || 'Prediction failed.'); return; }
      render(result, body);
    } catch (err) {
      formError('Could not reach the server. Is the app still running?');
    } finally {
      button.removeAttribute('aria-busy'); button.disabled = false;
      $('predict-btn-text').textContent = 'Predict waste';
    }
  });
  Object.keys(FIELDS).forEach((n) => $(n).addEventListener('input', () => setError(n, '')));
})();
