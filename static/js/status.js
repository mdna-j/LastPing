async function loadStatus() {
  const root = document.getElementById('statusRoot');
  if (!root) return;
  const projectId = root.getAttribute('data-project-id');
  root.innerHTML = '<div class="muted">Loading...</div>';
  const resp = await fetch(`/ui/status/${projectId}/data`);
  if (!resp.ok) {
    root.innerHTML = '<div class="muted">Failed to load status</div>';
    return;
  }
  const data = await resp.json();
  const checks = data.checks || [];
  const downCount = checks.filter(c => (c.status || '').toLowerCase() === 'down').length;

  const summary = `
    <div class="card">
      <div><strong>${data.project.name}</strong></div>
      <div class="muted">Checks: ${checks.length} • Down: ${downCount}</div>
    </div>
  `;

  const checksHtml = checks.map(c => {
    const status = (c.status || 'unknown').toLowerCase();
    const badgeClass = status === 'down' ? 'badge status-down' : 'badge status-up';
    const lastEvent = c.last_event ? `${c.last_event.type} @ ${c.last_event.created_at}` : 'n/a';
    const lastPing = c.last_ping || 'n/a';
    return `
      <div class="card">
        <div><strong>${c.name}</strong> <span class="${badgeClass}">${status.toUpperCase()}</span></div>
        <div class="muted">Type: ${c.type} • Last ping: ${lastPing}</div>
        <div class="muted">Last event: ${lastEvent}</div>
      </div>
    `;
  }).join('');

  const incidents = data.open_incidents || [];
  const incidentsHtml = incidents.length
    ? incidents.map(i => `<div class="card"><div><strong>Incident ${i.id}</strong> • Check ${i.check_id}</div><div class="muted">Started: ${i.started_at}</div></div>`).join('')
    : '<div class="muted">No open incidents</div>';

  root.innerHTML = `
    ${summary}
    <h2>Checks</h2>
    ${checksHtml || '<div class="muted">No checks</div>'}
    <h2>Open Incidents</h2>
    ${incidentsHtml}
  `;
}

document.addEventListener('DOMContentLoaded', loadStatus);
