// Dashboard behaviour: prediction form + charts. All server text is inserted with textContent
// (never innerHTML), so user-provided content cannot inject markup.
(function () {
  const GREEN = '#1f6f43';
  const RISK_CLASS = { LOW: 'text-success', MEDIUM: 'text-warning', HIGH: 'text-danger' };
  const DECISION_TEXT = { DONATE: 'Donate', DISPOSE: 'Dispose', DO_NOT_DONATE: 'Do not donate' };
  const $ = (id) => document.getElementById(id);

  // ---------- charts ----------
  const dataEl = $('page-data');
  const data = dataEl ? JSON.parse(dataEl.textContent) : null;
  if (data && window.Chart) {
    const noLegend = { plugins: { legend: { display: false } }, maintainAspectRatio: false };
    const make = (id, config) => { const el = $(id); if (el) new Chart(el, config); };

    make('chart-weekday', {
      type: 'bar',
      data: { labels: data.days, datasets: [{ data: data.weekday_avg, backgroundColor: GREEN }] },
      options: { ...noLegend, scales: { y: { title: { display: true, text: 'kg per day' } } } },
    });
    make('chart-history', {
      type: 'line',
      data: { labels: data.history_labels, datasets: [{ data: data.history_values, borderColor: GREEN,
                                                          backgroundColor: 'rgba(31,111,67,.15)', fill: true, tension: .25 }] },
      options: { ...noLegend, scales: { y: { beginAtZero: true, title: { display: true, text: 'predicted kg' } } } },
    });
    // largest first (JSON from the server has its keys in alphabetical order)
    const names = Object.keys(data.importances).sort((a, b) => data.importances[b] - data.importances[a]);
    make('chart-importance', {
      type: 'bar',
      data: { labels: names, datasets: [{ data: names.map((n) => data.importances[n]), backgroundColor: GREEN }] },
      options: { ...noLegend, indexAxis: 'y' },
    });
    make('chart-status', {
      type: 'doughnut',
      data: { labels: ['Pending', 'Picked Up', 'Delivered'], datasets: [{ data: data.status_counts,
              backgroundColor: ['#adb5bd', '#0dcaf0', GREEN] }] },
      options: { maintainAspectRatio: false },
    });
  }

  // ---------- prediction form ----------
  const form = $('predict-form');
  if (!form) return;

  function show(id, visible) { $(id).classList.toggle('d-none', !visible); }

  function showError(message) {
    show('result-empty', false); show('result-box', false);
    $('result-error').textContent = message;
    show('result-error', true);
  }

  function showResult(r) {
    show('result-empty', false); show('result-error', false); show('result-box', true);

    const kg = Number(r.waste_kg).toFixed(1);
    $('card-waste').textContent = kg + ' kg';
    const risk = $('card-risk');
    risk.textContent = r.risk.charAt(0) + r.risk.slice(1).toLowerCase();
    risk.className = 'stat-value ' + (RISK_CLASS[r.risk] || '');
    $('card-decision').textContent = DECISION_TEXT[r.decision] || r.decision;

    $('result-summary').textContent =
      'Expected waste: ' + kg + ' kg. Risk level: ' + r.risk + '. Recommendation: ' +
      (DECISION_TEXT[r.decision] || r.decision) + '.';
    $('result-reason').textContent = r.reason;

    $('result-warning').textContent = r.warning || '';
    show('result-warning', !!r.warning);

    const donate = r.decision === 'DONATE';
    $('result-ngo').textContent = donate ? 'Suggested NGO: ' + r.suggested_ngo + ' (donate up to ' + r.donate_kg + ' kg)' : '';
    show('result-ngo', donate);
    const link = $('result-donate');
    link.href = '/donate?qty=' + encodeURIComponent(r.donate_kg);
    show('result-donate', donate);
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('predict-btn');
    button.disabled = true;
    try {
      const body = Object.fromEntries(new FormData(form).entries());
      const response = await fetch('/predict', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({ error: 'Unexpected response from the server.' }));
      if (!response.ok) { showError(result.error || 'Prediction failed.'); return; }
      showResult(result);
    } catch (err) {
      showError('Could not reach the server. Is the app still running?');
    } finally {
      button.disabled = false;
    }
  });
})();
