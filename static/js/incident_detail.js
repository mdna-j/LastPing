function incidentIdFromPath(){
  const parts = location.pathname.split("/");
  return parts[parts.length - 1];
}

function incidentProjectId(){
  return document.getElementById("projectId").value || "1";
}

function escapeHtml(value){
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function readHeaders(){
  const headers = {};
  const apiKey = document.getElementById("apiKey") ? document.getElementById("apiKey").value.trim() : "";
  const userToken = document.getElementById("userToken") ? document.getElementById("userToken").value.trim() : "";
  const adminToken = document.getElementById("adminToken") ? document.getElementById("adminToken").value.trim() : "";
  if(apiKey) headers["X-API-KEY"] = apiKey;
  if(userToken) headers.Authorization = `Bearer ${userToken}`;
  if(adminToken) headers["X-ADMIN-TOKEN"] = adminToken;
  return headers;
}

function manageHeaders(){
  return {...readHeaders(), "Content-Type": "application/json"};
}

function formatTimestamp(value){
  if(!value) return "n/a";
  try{
    return new Date(value).toLocaleString();
  }catch(_e){
    return value;
  }
}

function renderTimeline(timeline, stats){
  const timelineEl = document.getElementById("incidentTimeline");
  const statsEl = document.getElementById("timelineStats");
  if(statsEl){
    if(stats){
      statsEl.innerHTML = `
        <div class="incident-meta-grid">
          <div><span class="muted">Events</span><div>${stats.events || 0}</div></div>
          <div><span class="muted">Notes</span><div>${stats.notes || 0}</div></div>
          <div><span class="muted">Alerts</span><div>${stats.alerts || 0}</div></div>
          <div><span class="muted">Remediation</span><div>${stats.remediation_steps || 0}</div></div>
        </div>
      `;
    }else{
      statsEl.innerHTML = `<div class="muted">No timeline metrics available.</div>`;
    }
  }
  if(!timelineEl) return;
  if(!timeline || !timeline.length){
    timelineEl.innerHTML = `<div class="muted">No timeline entries yet.</div>`;
    return;
  }
  timelineEl.innerHTML = timeline.map((item)=> `
    <div class="card incident-timeline-item">
      <div class="incident-card-head">
        <div>
          <strong>${escapeHtml(item.title || "Timeline entry")}</strong>
          <span class="badge status-up">${escapeHtml(item.kind || "timeline")}</span>
        </div>
        <span class="muted">${formatTimestamp(item.ts)}</span>
      </div>
      <div>${escapeHtml(item.summary || "")}</div>
      ${item.actor ? `<div class="muted">Actor: ${escapeHtml(item.actor)}</div>` : ""}
    </div>
  `).join("");
}

function renderNotes(notes){
  const notesEl = document.getElementById("notesList");
  if(!notesEl) return;
  if(!notes || !notes.length){
    notesEl.innerHTML = `<div class="muted">No notes yet.</div>`;
    return;
  }
  notesEl.innerHTML = notes.map((note)=> `
    <div class="card incident-note-card">
      <div class="incident-card-head">
        <strong>${escapeHtml(note.author || "unknown")}</strong>
        <span class="muted">${formatTimestamp(note.created_at)}</span>
      </div>
      <div>${escapeHtml(note.body)}</div>
    </div>
  `).join("");
}

function renderArtifacts(artifacts){
  const artifactsEl = document.getElementById("incidentArtifacts");
  if(!artifactsEl) return;
  if(!artifacts || !artifacts.length){
    artifactsEl.innerHTML = `<div class="muted">No browser artifacts linked yet.</div>`;
    return;
  }
  artifactsEl.innerHTML = artifacts.map((artifact)=> `
    <div class="card incident-note-card">
      <div class="incident-card-head">
        <strong>${escapeHtml(artifact.artifact_type || "artifact")}</strong>
        <span class="muted">${formatTimestamp(artifact.created_at)}</span>
      </div>
      <div class="incident-meta-grid">
        <div><span class="muted">File</span><div>${escapeHtml(artifact.file_name || "artifact")}</div></div>
        <div><span class="muted">Size</span><div>${artifact.size_bytes ? `${artifact.size_bytes} bytes` : "n/a"}</div></div>
        <div><span class="muted">Check Result</span><div>${artifact.check_result_id || "n/a"}</div></div>
      </div>
      <div style="margin-top:8px">
        <a class="btn btn-secondary" href="${escapeHtml(artifact.download_url)}">Download</a>
      </div>
    </div>
  `).join("");
}

function renderIncidentSummary(incident){
  const summaryEl = document.getElementById("incidentSummary");
  const actionsEl = document.getElementById("incidentActions");
  const jiraInfoEl = document.getElementById("jiraTicketInfo");
  const resolutionMetaEl = document.getElementById("resolutionMeta");
  const resolutionInput = document.getElementById("resolutionSummary");
  if(!summaryEl) return;
  const owner = incident.owner ? escapeHtml(incident.owner) : "unassigned";
  const ack = incident.acknowledged_at
    ? `${formatTimestamp(incident.acknowledged_at)} by ${escapeHtml(incident.acknowledged_by || "unknown")}`
    : "not acknowledged";
  const resolved = incident.resolved_at
    ? `${formatTimestamp(incident.resolved_at)} by ${escapeHtml(incident.resolved_by || "system")}`
    : "still open";
  const silence = incident.silenced_until
    ? `${formatTimestamp(incident.silenced_until)} by ${escapeHtml(incident.silenced_by || "unknown")}`
    : "not silenced";
  const jiraLink = incident.jira_issue_url
    ? `<a href="${escapeHtml(incident.jira_issue_url)}" target="_blank" rel="noreferrer">${escapeHtml(incident.jira_issue_key || incident.jira_issue_url)}</a>`
    : "not created";
  summaryEl.innerHTML = `
    <div class="incident-meta-grid">
      <div><span class="muted">Incident</span><div>${incident.id}</div></div>
      <div><span class="muted">Check</span><div>${incident.check_id}</div></div>
      <div><span class="muted">Status</span><div>${escapeHtml(incident.status || "unknown")}</div></div>
      <div><span class="muted">Started</span><div>${formatTimestamp(incident.started_at)}</div></div>
      <div><span class="muted">Resolved</span><div>${resolved}</div></div>
      <div><span class="muted">Owner</span><div>${owner}</div></div>
      <div><span class="muted">Acknowledged</span><div>${ack}</div></div>
      <div><span class="muted">Silenced</span><div>${silence}</div></div>
      <div><span class="muted">Notes</span><div>${incident.note_count || 0}</div></div>
      <div><span class="muted">Jira</span><div>${jiraLink}</div></div>
    </div>
  `;
  if(jiraInfoEl){
    jiraInfoEl.innerHTML = incident.jira_issue_url
      ? `Linked Jira issue: <a href="${escapeHtml(incident.jira_issue_url)}" target="_blank" rel="noreferrer">${escapeHtml(incident.jira_issue_key || incident.jira_issue_url)}</a>`
      : "No Jira ticket linked yet.";
  }
  if(resolutionInput){
    resolutionInput.value = incident.resolution_summary || "";
  }
  if(resolutionMetaEl){
    resolutionMetaEl.innerHTML = incident.resolution_summary
      ? `Current summary: ${escapeHtml(incident.resolution_summary)}`
      : "No manual resolution summary recorded yet.";
  }
  if(actionsEl){
    actionsEl.classList.remove("hidden");
    const ackBtn = document.getElementById("ackIncidentBtn");
    const silenceBtn = document.getElementById("silenceIncidentBtn");
    const jiraBtn = document.getElementById("jiraTicketBtn");
    const resolveBtn = document.getElementById("resolveIncidentBtn");
    const reopenBtn = document.getElementById("reopenIncidentBtn");
    if(ackBtn){
      ackBtn.textContent = incident.acknowledged_at ? "Clear Ack" : "Acknowledge";
      ackBtn.dataset.acknowledged = incident.acknowledged_at ? "false" : "true";
    }
    if(silenceBtn){
      silenceBtn.textContent = incident.silenced_until ? "Extend Snooze" : "Snooze";
    }
    if(jiraBtn){
      jiraBtn.textContent = incident.jira_issue_url ? "Open Jira Ticket" : "Create Jira Ticket";
      jiraBtn.dataset.issueUrl = incident.jira_issue_url || "";
    }
    if(resolveBtn){
      resolveBtn.textContent = incident.resolved_at ? "Update Resolution" : "Resolve Incident";
    }
    if(reopenBtn){
      reopenBtn.classList.toggle("hidden", !incident.resolved_at || incident.status === "merged");
    }
  }
}

function renderEvents(json){
  const el = document.getElementById("detail");
  if(!el) return;
  const events = (json.events || []).map((event)=> `
    <div class="card">
      <label>
        <input type="checkbox" data-eid="${event.id}"/>
        <strong>${escapeHtml(event.type)}</strong>
        <span class="muted">${formatTimestamp(event.ts)}</span>
      </label>
      <div>${escapeHtml(event.message || "")}</div>
    </div>
  `).join("");
  el.innerHTML = `${events}<div style="margin-top:8px"><button id="splitBtn" class="btn btn-secondary">Split Selected</button></div>`;
}

async function loadIncidentDetail(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const detailEl = document.getElementById("detail");
  if(detailEl) detailEl.innerText = "Loading...";
  const resp = await fetch(`/projects/${pid}/incidents/${iid}`, {headers: readHeaders()});
  if(!resp.ok){
    if(detailEl) detailEl.innerText = "Failed to load";
    return null;
  }
  const json = await resp.json();
  renderIncidentSummary(json.incident);
  renderEvents(json);
  renderNotes(json.notes || []);
  renderArtifacts(json.artifacts || []);
  renderTimeline(json.timeline || [], json.timeline_stats || null);
  const shareInfo = document.getElementById("shareInfo");
  if(shareInfo){
    shareInfo.innerText = json.incident.share_token
      ? `Public: ${location.origin}/ui/incidents/public/${json.incident.share_token}`
      : "";
  }
  return json;
}

async function loadSimilarIncidents(){
  const el = document.getElementById("similarIncidents");
  if(!el) return;
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  el.innerText = "Loading...";
  try{
    const resp = await fetch(`/projects/${pid}/analytics/incident-similarity?incident_id=${iid}`, {headers: readHeaders()});
    if(!resp.ok){
      el.innerText = "Failed to load similar incidents";
      return;
    }
    const json = await resp.json();
    const matches = json.matches || [];
    if(!matches.length){
      el.innerHTML = `<div class="muted">No similar incidents found.</div>`;
      return;
    }
    el.innerHTML = matches.map((match)=> `
      <div class="card">
        <div><strong>Incident ${match.incident_id}</strong> (check ${match.check_id})</div>
        <div class="muted">score ${match.score} | started ${formatTimestamp(match.started_at)}</div>
        <div style="margin-top:6px"><a class="btn btn-secondary" href="/ui/incidents/${match.incident_id}?project=${pid}">Open</a></div>
      </div>
    `).join("");
  }catch(_e){
    el.innerText = "Failed to load similar incidents";
  }
}

async function createShare(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method: "POST", headers: readHeaders()});
  if(!resp.ok){
    alert("Failed to create share link.");
    return;
  }
  const json = await resp.json();
  const shareInfo = document.getElementById("shareInfo");
  if(shareInfo){
    shareInfo.innerText = `Public: ${location.origin}/ui/incidents/public/${json.share_token}`;
  }
}

async function createJiraTicket(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const jiraBtn = document.getElementById("jiraTicketBtn");
  if(jiraBtn && jiraBtn.dataset.issueUrl){
    window.open(jiraBtn.dataset.issueUrl, "_blank", "noopener,noreferrer");
    return;
  }
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/jira-ticket`, {method: "POST", headers: readHeaders()});
  const body = await resp.json().catch(()=> ({}));
  if(!resp.ok){
    alert(body && body.detail ? body.detail : "Failed to create Jira ticket.");
    return;
  }
  if(body && body.message){
    alert(body.message);
  }
  await refreshIncidentPage();
}

async function downloadPostmortem(format){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const path = format === "pdf"
    ? `/projects/${pid}/incidents/${iid}/postmortem.pdf`
    : `/projects/${pid}/incidents/${iid}/postmortem.md`;
  const resp = await fetch(path, {headers: readHeaders()});
  if(!resp.ok){
    alert(`Failed to export ${format.toUpperCase()} postmortem.`);
    return;
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : `incident-${iid}-postmortem.${format}`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function assignOwner(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const owner = prompt(`Assign owner for incident ${iid} (leave blank to clear):`, "");
  if(owner === null) return;
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/assign`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({owner: owner.trim() ? owner.trim() : null}),
  });
  if(!resp.ok){
    alert("Failed to update owner.");
    return;
  }
  await refreshIncidentPage();
}

async function updateAck(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const ackBtn = document.getElementById("ackIncidentBtn");
  const acknowledged = ackBtn && ackBtn.dataset.acknowledged === "true";
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/ack`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({acknowledged}),
  });
  if(!resp.ok){
    alert("Failed to update acknowledgement.");
    return;
  }
  await refreshIncidentPage();
}

async function silenceIncident(clearSilence){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  let payload = {clear: true};
  if(!clearSilence){
    const minutesRaw = prompt(`Silence incident ${iid} for how many minutes?`, "60");
    if(!minutesRaw) return;
    const minutes = parseInt(minutesRaw, 10);
    if(Number.isNaN(minutes) || minutes <= 0){
      alert("Enter a positive number of minutes.");
      return;
    }
    payload = {until: new Date(Date.now() + minutes * 60 * 1000).toISOString()};
  }
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/silence`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify(payload),
  });
  if(!resp.ok){
    alert("Failed to update incident silence.");
    return;
  }
  await refreshIncidentPage();
}

async function addIncidentNote(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const bodyEl = document.getElementById("incidentNoteBody");
  const body = bodyEl ? bodyEl.value.trim() : "";
  if(!body){
    alert("Enter a note first.");
    return;
  }
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/notes`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({body}),
  });
  if(!resp.ok){
    alert("Failed to add note.");
    return;
  }
  if(bodyEl) bodyEl.value = "";
  await refreshIncidentPage();
}

async function resolveIncident(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const summaryEl = document.getElementById("resolutionSummary");
  const summary = summaryEl ? summaryEl.value.trim() : "";
  if(summary.length < 3){
    alert("Add a short resolution summary before resolving the incident.");
    return;
  }
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/resolve`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({summary}),
  });
  const body = await resp.json().catch(()=> ({}));
  if(!resp.ok){
    alert(body && body.detail ? body.detail : "Failed to resolve incident.");
    return;
  }
  await refreshIncidentPage();
}

async function reopenIncident(){
  const iid = incidentIdFromPath();
  const pid = incidentProjectId();
  const reason = prompt(`Why is incident ${iid} being reopened?`, "") || "";
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/reopen`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({reason: reason.trim() || null}),
  });
  const body = await resp.json().catch(()=> ({}));
  if(!resp.ok){
    alert(body && body.detail ? body.detail : "Failed to reopen incident.");
    return;
  }
  await refreshIncidentPage();
}

async function refreshIncidentPage(){
  await loadIncidentDetail();
  await loadSimilarIncidents();
}

function applyQueryParams(){
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project");
  if(project && document.getElementById("projectId")){
    document.getElementById("projectId").value = project;
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  applyQueryParams();
  const shareBtn = document.getElementById("shareBtn");
  const jiraTicketBtn = document.getElementById("jiraTicketBtn");
  const refreshBtn = document.getElementById("reloadIncidentBtn");
  const assignBtn = document.getElementById("assignOwnerBtn");
  const ackBtn = document.getElementById("ackIncidentBtn");
  const silenceBtn = document.getElementById("silenceIncidentBtn");
  const clearSilenceBtn = document.getElementById("clearSilenceBtn");
  const addNoteBtn = document.getElementById("addNoteBtn");
  const resolveBtn = document.getElementById("resolveIncidentBtn");
  const reopenBtn = document.getElementById("reopenIncidentBtn");
  const exportMarkdownBtn = document.getElementById("exportMarkdownBtn");
  const exportPdfBtn = document.getElementById("exportPdfBtn");
  if(shareBtn) shareBtn.addEventListener("click", createShare);
  if(jiraTicketBtn) jiraTicketBtn.addEventListener("click", createJiraTicket);
  if(refreshBtn) refreshBtn.addEventListener("click", refreshIncidentPage);
  if(assignBtn) assignBtn.addEventListener("click", assignOwner);
  if(ackBtn) ackBtn.addEventListener("click", updateAck);
  if(silenceBtn) silenceBtn.addEventListener("click", ()=> silenceIncident(false));
  if(clearSilenceBtn) clearSilenceBtn.addEventListener("click", ()=> silenceIncident(true));
  if(addNoteBtn) addNoteBtn.addEventListener("click", addIncidentNote);
  if(resolveBtn) resolveBtn.addEventListener("click", resolveIncident);
  if(reopenBtn) reopenBtn.addEventListener("click", reopenIncident);
  if(exportMarkdownBtn) exportMarkdownBtn.addEventListener("click", ()=> downloadPostmortem("md"));
  if(exportPdfBtn) exportPdfBtn.addEventListener("click", ()=> downloadPostmortem("pdf"));
  refreshIncidentPage();

  document.addEventListener("click", async (event)=>{
    if(event.target && event.target.id === "splitBtn"){
      const iid = incidentIdFromPath();
      const pid = incidentProjectId();
      const checked = Array.from(document.querySelectorAll('#detail input[type="checkbox"]:checked'))
        .map((input)=> parseInt(input.getAttribute("data-eid"), 10));
      if(!checked.length){
        alert("No events selected.");
        return;
      }
      if(!confirm("Split selected events into a new incident?")) return;
      const btn = event.target;
      btn.disabled = true;
      try{
        const resp = await fetch(`/projects/${pid}/incidents/${iid}/split`, {
          method: "POST",
          headers: manageHeaders(),
          body: JSON.stringify({event_ids: checked}),
        });
        if(!resp.ok){
          alert("Split failed.");
          return;
        }
        const json = await resp.json();
        alert(`Split into incident ${json.split_into}`);
        await refreshIncidentPage();
      }finally{
        btn.disabled = false;
      }
    }
  });
});
