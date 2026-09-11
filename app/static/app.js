"use strict";

const state = {
  token: localStorage.getItem("hyperlite_token") || null,
  username: localStorage.getItem("hyperlite_username") || null,
  role: localStorage.getItem("hyperlite_role") || null,
};

// ---------- API helper ----------
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (state.token) opts.headers["Authorization"] = "Bearer " + state.token;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    let msg = "Erreur inconnue";
    if (data && data.detail) {
      msg = Array.isArray(data.detail) ? data.detail.join(" ; ") : data.detail;
    }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ---------- Toast ----------
let toastTimer = null;
function toast(message, type) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast show" + (type ? " " + type : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3500);
}

// ---------- Modal ----------
function openModal(html) {
  document.getElementById("modal-content").innerHTML = html;
  document.getElementById("modal-overlay").style.display = "flex";
}
function closeModal() {
  document.getElementById("modal-overlay").style.display = "none";
  document.getElementById("modal-content").innerHTML = "";
}
document.getElementById("modal-overlay").addEventListener("click", (e) => {
  if (e.target.id === "modal-overlay") closeModal();
});

// ---------- Auth ----------
function isAdmin() { return state.role === "admin"; }

async function tryRestoreSession() {
  if (!state.token) { showLogin(); return; }
  try {
    const me = await api("GET", "/auth/me");
    state.username = me.username;
    state.role = me.role;
    showApp();
  } catch (e) {
    logout(false);
  }
}

function showLogin() {
  document.getElementById("login-screen").style.display = "flex";
  document.getElementById("app-screen").style.display = "none";
}

function showApp() {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("app-screen").style.display = "block";
  document.getElementById("user-label").textContent = state.username;
  document.getElementById("role-badge").textContent = state.role;
  applyRoleVisibility();
  switchView("dashboard");
}

function applyRoleVisibility() {
  document.querySelectorAll(".admin-only").forEach((el) => {
    el.style.display = isAdmin() ? "" : "none";
  });
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  const errEl = document.getElementById("login-error");
  errEl.textContent = "";
  try {
    const body = new URLSearchParams({ username, password });
    const res = await fetch("/auth/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Identifiants invalides");
    state.token = data.access_token;
    state.username = username;
    state.role = data.role;
    localStorage.setItem("hyperlite_token", state.token);
    localStorage.setItem("hyperlite_username", state.username);
    localStorage.setItem("hyperlite_role", state.role);
    showApp();
  } catch (err) {
    errEl.textContent = err.message;
  }
});

function logout(showToast) {
  state.token = null; state.username = null; state.role = null;
  localStorage.removeItem("hyperlite_token");
  localStorage.removeItem("hyperlite_username");
  localStorage.removeItem("hyperlite_role");
  showLogin();
  if (showToast) toast("Deconnecte.");
}
document.getElementById("logout-btn").addEventListener("click", () => logout(true));

// ---------- Navigation ----------
function switchView(name) {
  document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  if (name === "dashboard") loadDashboard();
  if (name === "vms") loadVMs();
  if (name === "storage") loadStorage();
  if (name === "network") loadNetworks();
  if (name === "templates") loadTemplates();
  if (name === "isos") loadIsos();
}
document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

// ---------- Dashboard ----------
async function loadDashboard() {
  const el = document.getElementById("dashboard-cards");
  el.innerHTML = "<p>Chargement...</p>";
  try {
    const d = await api("GET", "/dashboard");
    const okDot = d.hyperviseur.connecte ? "ok" : "bad";
    el.innerHTML = `
      <div class="card">
        <div class="label">Hyperviseur</div>
        <div class="value"><span class="dot ${okDot}"></span>${d.hyperviseur.nom}</div>
        <div class="sub">${d.hyperviseur.type} — ${d.hyperviseur.connecte ? "connecte" : "deconnecte"}</div>
      </div>
      <div class="card">
        <div class="label">Machines virtuelles</div>
        <div class="value">${d.vms.total}</div>
        <div class="sub">${d.vms.actives} active(s) · ${d.vms.arretees} arretee(s)</div>
      </div>
      <div class="card">
        <div class="label">Memoire disponible</div>
        <div class="value">${d.memoire_disponible_mo != null ? Math.round(d.memoire_disponible_mo) + " Mo" : "n/a"}</div>
      </div>
      <div class="card">
        <div class="label">Stockage</div>
        <div class="value">${d.stockage.disponible_go != null ? d.stockage.disponible_go + " Go" : "n/a"}</div>
        <div class="sub">disponible sur ${d.stockage.capacite_go != null ? d.stockage.capacite_go + " Go" : "n/a"}</div>
      </div>
    `;
  } catch (e) {
    el.innerHTML = `<p class="error">Erreur : ${e.message}</p>`;
  }
}

// ---------- VMs ----------
function stateBadgeClass(etat) {
  if (etat === "actif") return "actif";
  if (etat === "arrete") return "arrete";
  return "autre";
}

async function loadVMs() {
  const tbody = document.getElementById("vms-tbody");
  tbody.innerHTML = `<tr class="empty-row"><td colspan="6">Chargement...</td></tr>`;
  try {
    const vms = await api("GET", "/vms");
    if (!vms.length) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="6">Aucune VM pour le moment.</td></tr>`;
      return;
    }
    tbody.innerHTML = vms.map((vm) => `
      <tr>
        <td><a href="#" class="vm-link" data-name="${vm.nom}">${vm.nom}</a></td>
        <td><span class="badge ${stateBadgeClass(vm.etat)}">${vm.etat}</span></td>
        <td>${vm.vcpu}</td>
        <td>${Math.round(vm.memoire_mo)} Mo</td>
        <td>${vm.ip || "—"}</td>
        <td>
          <button class="btn-small btn-secondary" data-action="ssh" data-name="${vm.nom}">SSH</button>
          <button class="btn-small btn-secondary admin-only" data-action="start" data-name="${vm.nom}" ${vm.etat === "actif" ? "disabled" : ""}>Demarrer</button>
          <button class="btn-small btn-secondary admin-only" data-action="stop" data-name="${vm.nom}" ${vm.etat !== "actif" ? "disabled" : ""}>Arreter</button>
          <button class="btn-small btn-secondary admin-only" data-action="restart" data-name="${vm.nom}" ${vm.etat !== "actif" ? "disabled" : ""}>Redemarrer</button>
          <button class="btn-small btn-secondary admin-only" data-action="console" data-name="${vm.nom}" ${vm.etat !== "actif" ? "disabled" : ""}>Console</button>
          <button class="btn-small btn-secondary admin-only" data-action="terminal" data-name="${vm.nom}" ${vm.etat !== "actif" ? "disabled" : ""}>Terminal</button>
          <button class="btn-small btn-secondary admin-only" data-action="clone" data-name="${vm.nom}" ${vm.etat === "actif" ? "disabled" : ""}>Cloner</button>
          <button class="btn-small btn-secondary admin-only" data-action="totemplate" data-name="${vm.nom}" ${vm.etat === "actif" ? "disabled" : ""}>Vers template</button>
          <button class="btn-small btn-danger admin-only" data-action="delete" data-name="${vm.nom}" ${vm.etat === "actif" ? "disabled" : ""}>Supprimer</button>
        </td>
      </tr>
    `).join("");
    applyRoleVisibility();
    tbody.querySelectorAll(".vm-link").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault(); openVMDetails(a.dataset.name);
    }));
    tbody.querySelectorAll("button[data-action]").forEach((btn) => btn.addEventListener("click", () => handleVMAction(btn.dataset.action, btn.dataset.name)));
  } catch (e) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="6">Erreur : ${e.message}</td></tr>`;
  }
}

async function copySSHCommand(name) {
  try {
    const vm = await api("GET", `/vms/${encodeURIComponent(name)}`);
    if (!vm.ip) { toast("Aucune adresse IP connue (la VM est-elle demarree ?).", "error"); return; }
    const cmd = `ssh ${vm.utilisateur_ssh || "<utilisateur inconnu>"}@${vm.ip}`;
    try {
      await navigator.clipboard.writeText(cmd);
      toast(`Commande copiee : ${cmd}`, "success");
    } catch (e) {
      toast(`Commande SSH : ${cmd}`, "success");
    }
  } catch (e) { toast(e.message, "error"); }
}

async function handleVMAction(action, name) {
  if (action === "ssh") { await copySSHCommand(name); return; }
  if (action === "console") { await openConsole(name); return; }
  if (action === "terminal") { await openTerminal(name); return; }
  try {
    if (action === "start") {
      await api("POST", `/vms/${encodeURIComponent(name)}/start`);
      toast(`VM '${name}' demarree.`, "success");
    } else if (action === "stop") {
      const force = confirm(`Arreter '${name}' proprement ? (Annuler = arret force)`);
      await api("POST", `/vms/${encodeURIComponent(name)}/stop?force=${force ? "false" : "true"}`);
      toast(`VM '${name}' arretee.`, "success");
    } else if (action === "restart") {
      await api("POST", `/vms/${encodeURIComponent(name)}/restart?force=true`);
      toast(`VM '${name}' redemarree.`, "success");
    } else if (action === "delete") {
      if (!confirm(`Supprimer definitivement la VM '${name}' et son disque ? Cette action est irreversible.`)) return;
      await api("DELETE", `/vms/${encodeURIComponent(name)}?confirm=true`);
      toast(`VM '${name}' supprimee.`, "success");
    } else if (action === "clone") {
      const newName = prompt(`Nom de la copie de '${name}' :`, `${name}-clone`);
      if (!newName || !newName.trim()) return;
      await api("POST", `/vms/${encodeURIComponent(name)}/clone`, { new_name: newName.trim() });
      toast(`VM '${name}' clonee vers '${newName.trim()}' (arretee).`, "success");
    } else if (action === "totemplate") {
      const tplName = prompt(`Nom du template a creer a partir de '${name}' :`, name);
      if (!tplName || !tplName.trim()) return;
      if (!confirm(`'${name}' sera convertie en template '${tplName.trim()}' et disparaitra de la liste des VMs. Continuer ?`)) return;
      await api("POST", `/templates/from-vm/${encodeURIComponent(name)}`, { template_name: tplName.trim() });
      toast(`Template '${tplName.trim()}' cree a partir de '${name}'.`, "success");
    }
    loadVMs();
    loadDashboard();
  } catch (e) {
    toast(e.message, "error");
  }
}

document.getElementById("create-vm-btn").addEventListener("click", async () => {
  let networks = [];
  try { networks = await api("GET", "/networks"); } catch (e) { /* ignore */ }
  let isos = [];
  try { isos = await api("GET", "/isos"); } catch (e) { /* ignore */ }
  const options = networks.map((n) => `<option value="${n.nom}" ${n.nom === "default" ? "selected" : ""}>${n.nom} (${n.type})</option>`).join("");
  const isoOptions = isos.map((i) => `<option value="${i.nom}">${i.nom}</option>`).join("");
  openModal(`
    <button class="close-x" onclick="closeModal()">&times;</button>
    <h2>Creer une VM</h2>
    <form id="create-vm-form">
      <div class="form-row"><label>Nom</label><input type="text" id="cv-name" required pattern="[a-zA-Z0-9][a-zA-Z0-9\\-]{1,62}" placeholder="ex. web-01"></div>
      <div class="form-row"><label>vCPU (1-2)</label><input type="number" id="cv-vcpu" min="1" max="2" value="1" required></div>
      <div class="form-row"><label>Memoire (Mo, 256-2048)</label><input type="number" id="cv-mem" min="256" max="2048" value="768" required></div>
      <div class="form-row"><label>Utilisateur</label><input type="text" id="cv-username" required pattern="[a-z_][a-z0-9_\\-]{0,31}" placeholder="ex. antho"></div>
      <div class="form-row"><label>Mot de passe</label><input type="password" id="cv-password" required minlength="4"></div>
      <div class="form-row"><label>Reseau</label><select id="cv-network">${options || '<option value="default">default</option>'}</select></div>
      <div class="form-row"><label>ISO au demarrage</label><select id="cv-iso"><option value="">Aucune (pas de media)</option>${isoOptions}</select></div>
      <div class="form-row" style="align-items:flex-start">
        <label>Disques (Go)</label>
        <div style="flex:1">
          <div id="cv-disks-list"></div>
          <button type="button" id="cv-add-disk-btn" class="btn-secondary" style="margin-top:6px;padding:4px 10px;font-size:12px">+ Ajouter un disque</button>
        </div>
      </div>
      <p class="form-error" id="cv-error"></p>
      <div class="form-actions">
        <button type="button" class="btn-secondary" onclick="closeModal()">Annuler</button>
        <button type="submit" class="btn-primary">Creer</button>
      </div>
    </form>
  `);

  const disksList = document.getElementById("cv-disks-list");
  function renumberDiskRows() {
    Array.from(disksList.children).forEach((row, i) => {
      row.querySelector(".cv-disk-letter").textContent = "sd" + String.fromCharCode(97 + i);
    });
  }
  function addDiskRow(sizeValue) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:6px";
    const letter = "sd" + String.fromCharCode(97 + disksList.children.length);
    row.innerHTML = `<span class="cv-disk-letter" style="font-family:monospace;width:32px">${letter}</span><input type="number" class="cv-disk-size" min="1" max="500" value="${sizeValue}" required style="width:80px"><span>Go</span><button type="button" class="btn-secondary cv-remove-disk" style="padding:2px 8px">&times;</button>`;
    disksList.appendChild(row);
    row.querySelector(".cv-remove-disk").addEventListener("click", () => {
      if (disksList.children.length <= 1) return;
      row.remove();
      renumberDiskRows();
    });
  }
  addDiskRow(10);
  document.getElementById("cv-add-disk-btn").addEventListener("click", () => addDiskRow(5));

  document.getElementById("create-vm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("cv-error");
    errEl.textContent = "";
    const diskSizes = Array.from(document.querySelectorAll(".cv-disk-size")).map((el) => parseInt(el.value, 10));
    if (diskSizes.length === 0) { errEl.textContent = "Ajoutez au moins un disque."; return; }
    const payload = {
      name: document.getElementById("cv-name").value.trim(),
      vcpu: parseInt(document.getElementById("cv-vcpu").value, 10),
      memory_mb: parseInt(document.getElementById("cv-mem").value, 10),
      disks: diskSizes.map((size_gb) => ({ size_gb })),
      network: document.getElementById("cv-network").value || "default",
      username: document.getElementById("cv-username").value.trim(),
      password: document.getElementById("cv-password").value,
    };
    const isoVal = document.getElementById("cv-iso").value;
    if (isoVal) payload.iso = isoVal;
    try {
      toast("Creation en cours (peut prendre un moment)...");
      await api("POST", "/vms", payload);
      toast(`VM '${payload.name}' creee.`, "success");
      closeModal();
      loadVMs();
      loadDashboard();
    } catch (err) {
      errEl.textContent = err.message;
    }
  });
});

// ---------- VM details modal ----------
async function openVMDetails(name) {
  openModal(`
    <button class="close-x" onclick="closeModal()">&times;</button>
    <h2>${name}</h2>
    <div class="tabs">
      <button class="tab-btn active" data-tab="info">Info</button>
      <button class="tab-btn" data-tab="disks">Disques</button>
      <button class="tab-btn" data-tab="cdrom">CD-ROM</button>
      <button class="tab-btn" data-tab="net">Reseau</button>
      <button class="tab-btn" data-tab="metrics">Metriques</button>
      <button class="tab-btn" data-tab="snap">Snapshots</button>
    </div>
    <div class="tab-pane active" id="tab-info"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-disks"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-cdrom"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-net"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-metrics"><p>Cliquez sur cet onglet pour mesurer.</p></div>
    <div class="tab-pane" id="tab-snap"><p>Chargement...</p></div>
  `);
  document.querySelectorAll(".tab-btn").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === "tab-" + btn.dataset.tab));
    if (btn.dataset.tab === "metrics") loadVMMetricsTab(name);
  }));

  loadVMInfoTab(name);
  loadVMDisksTab(name);
  loadVMCdromTab(name);
  loadVMNetTab(name);
  loadVMSnapTab(name);
}

async function loadVMMetricsTab(name) {
  const el = document.getElementById("tab-metrics");
  el.innerHTML = "<p>Mesure en cours (~0.5s)...</p>";
  try {
    const m = await api("GET", `/vms/${encodeURIComponent(name)}/metrics`);
    if (m.etat !== "actif") {
      el.innerHTML = `<p>VM arretee — pas de metriques en direct.</p>`;
      return;
    }
    const disksRows = (m.disques || []).map((d) => `<li><span>${d.cible}</span><span>lecture ${d.lecture_ko_s} Ko/s · ecriture ${d.ecriture_ko_s} Ko/s</span></li>`).join("");
    const netRows = (m.reseaux || []).map((n) => `<li><span>${n.interface}</span><span>reception ${n.reception_ko_s} Ko/s · emission ${n.emission_ko_s} Ko/s</span></li>`).join("");
    el.innerHTML = `
      <ul class="inline-list">
        <li><span>CPU</span><span>${m.cpu_pourcent}%</span></li>
        <li><span>Memoire</span><span>${m.memoire_utilisee_mo != null ? m.memoire_utilisee_mo + " Mo utilises" : "n/a"} / ${m.memoire_allouee_mo} Mo allouee</span></li>
      </ul>
      <h3 style="font-size:13px;color:var(--navy);margin-top:14px">Disques</h3>
      <ul class="inline-list">${disksRows || "<li>Aucun.</li>"}</ul>
      <h3 style="font-size:13px;color:var(--navy);margin-top:14px">Reseau</h3>
      <ul class="inline-list">${netRows || "<li>Aucun.</li>"}</ul>
      <div class="form-actions"><button class="btn-secondary" id="metrics-refresh-btn">Actualiser</button></div>
    `;
    document.getElementById("metrics-refresh-btn").addEventListener("click", () => loadVMMetricsTab(name));
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function loadVMInfoTab(name) {
  const el = document.getElementById("tab-info");
  try {
    const vm = await api("GET", `/vms/${encodeURIComponent(name)}`);
    el.innerHTML = `
      <ul class="inline-list">
        <li><span>Etat</span><span class="badge ${stateBadgeClass(vm.etat)}">${vm.etat}</span></li>
        <li><span>vCPU</span><span>${vm.vcpu}</span></li>
        <li><span>Memoire</span><span>${Math.round(vm.memoire_mo)} Mo</span></li>
        <li><span>IP</span><span>${vm.ip || "—"}</span></li>
        <li><span>Utilisateur SSH</span><span>${vm.utilisateur_ssh || "inconnu (ssh-copy-id manuel requis)"}</span></li>
        <li><span>UUID</span><span style="font-size:11px">${vm.uuid}</span></li>
      </ul>
      <div class="form-actions">
        <button class="btn-secondary" id="info-ssh-btn">Copier la commande SSH</button>
        <button class="btn-primary admin-only" id="info-console-btn" ${vm.etat !== "actif" ? "disabled" : ""}>Ouvrir la console</button>
        <button class="btn-primary admin-only" id="info-terminal-btn" ${vm.etat !== "actif" ? "disabled" : ""}>Ouvrir le terminal</button>
      </div>
    `;
    applyRoleVisibility();
    document.getElementById("info-ssh-btn").addEventListener("click", () => copySSHCommand(name));
    document.getElementById("info-console-btn").addEventListener("click", () => openConsole(name));
    document.getElementById("info-terminal-btn").addEventListener("click", () => openTerminal(name));
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

function nextScsiDev(disks) {
  // Un nom de device doit etre unique sur la VM quel que soit son type (disque ou
  // cdrom) : on prend donc toutes les cibles sdX deja occupees, cdrom inclus.
  const used = new Set(disks.filter((d) => /^sd[a-z]$/.test(d.cible)).map((d) => d.cible));
  for (const letter of "abcdefghijklmnopqrstuvwxyz") {
    if (!used.has("sd" + letter)) return "sd" + letter;
  }
  return null;
}

async function loadVMDisksTab(name) {
  const el = document.getElementById("tab-disks");
  try {
    const disks = await api("GET", `/vms/${encodeURIComponent(name)}/disks`);
    let volumes = [];
    try { volumes = await api("GET", "/storage/default/volumes"); } catch (e) { /* ignore */ }
    const freeVolumes = volumes.filter((v) => !v.utilise);
    const nextDev = nextScsiDev(disks);

    const rows = disks.map((d) => `
      <li>
        <span>${d.cible} (${d.bus || "?"}) ${d.type === "cdrom" ? "— cloud-init" : ""}</span>
        ${d.type !== "cdrom" && d.cible !== "vda" && d.cible !== "sda" ? `<button class="btn-small btn-danger admin-only" data-dev="${d.cible}">Detacher</button>` : ""}
      </li>`).join("");

    const volOptions = freeVolumes.map((v) => `<option value="${v.nom}">${v.nom} (${v.capacite_go} Go)</option>`).join("");
    el.innerHTML = `
      <ul class="inline-list">${rows || "<li>Aucun disque.</li>"}</ul>
      <div class="form-row admin-only" style="margin-top:14px;align-items:flex-start">
        <label>Ajouter un peripherique</label>
        <div style="flex:1">
          <select id="attach-source" style="width:100%;margin-bottom:6px">
            <option value="__new__">+ Nouveau disque...</option>
            ${volOptions}
          </select>
          <div id="attach-new-row" style="display:flex;gap:6px;margin-bottom:6px">
            <input type="text" id="attach-new-name" placeholder="nom du volume" value="${name}-disk-${Date.now().toString().slice(-5)}" style="flex:1">
            <input type="number" id="attach-new-size" min="1" max="500" value="5" style="width:70px">
            <span style="align-self:center">Go</span>
          </div>
          <div style="display:flex;gap:6px;align-items:center">
            ${nextDev ? `<span style="font-family:monospace">sera attache en tant que <b>${nextDev}</b></span>` : `<span class="error">Plus de lettre disponible (26 disques max)</span>`}
            <button class="btn-secondary" id="attach-vol-btn" style="margin-left:auto" ${nextDev ? "" : "disabled"}>Attacher</button>
          </div>
        </div>
      </div>
    `;
    applyRoleVisibility();
    el.querySelectorAll("button[data-dev]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api("DELETE", `/vms/${encodeURIComponent(name)}/disks/${encodeURIComponent(btn.dataset.dev)}`);
        toast("Disque detache.", "success");
        loadVMDisksTab(name);
      } catch (e) { toast(e.message, "error"); }
    }));

    const sourceSelect = document.getElementById("attach-source");
    const newRow = document.getElementById("attach-new-row");
    if (sourceSelect) {
      const syncNewRow = () => { newRow.style.display = sourceSelect.value === "__new__" ? "flex" : "none"; };
      sourceSelect.addEventListener("change", syncNewRow);
      syncNewRow();
    }

    const attachBtn = document.getElementById("attach-vol-btn");
    if (attachBtn) attachBtn.addEventListener("click", async () => {
      if (!nextDev) return;
      try {
        let volName = sourceSelect.value;
        if (volName === "__new__") {
          const newName = document.getElementById("attach-new-name").value.trim();
          const sizeGb = parseInt(document.getElementById("attach-new-size").value, 10);
          if (!newName) { toast("Indiquez un nom de volume.", "error"); return; }
          const created = await api("POST", "/storage/default/volumes", { name: newName, size_gb: sizeGb });
          volName = created.nom;
        }
        await api("POST", `/vms/${encodeURIComponent(name)}/disks`, { volume_name: volName, pool: "default", target_dev: nextDev });
        toast(`Disque '${volName}' attache en tant que ${nextDev}.`, "success");
        loadVMDisksTab(name);
      } catch (e) { toast(e.message, "error"); }
    });
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function loadVMCdromTab(name) {
  const el = document.getElementById("tab-cdrom");
  try {
    const disks = await api("GET", `/vms/${encodeURIComponent(name)}/disks`);
    const isos = await api("GET", "/isos");
    const cdrom = disks.find((d) => d.type === "cdrom");
    const current = cdrom && cdrom.source ? cdrom.source.split("/").pop() : null;
    const options = isos.map((i) => `<option value="${i.nom}">${i.nom} (${i.taille_mo} Mo)</option>`).join("");
    el.innerHTML = `
      <ul class="inline-list">
        <li><span>ISO montee</span><span>${current || "aucune"}</span></li>
      </ul>
      <div class="form-row admin-only" style="margin-top:14px">
        <label>Monter une ISO</label>
        <div style="display:flex; gap:6px">
          <select id="cdrom-select" style="flex:1">${options || '<option value="">Aucune ISO disponible</option>'}</select>
          <button class="btn-secondary" id="cdrom-mount-btn">Monter</button>
          ${current ? '<button class="btn-danger" id="cdrom-eject-btn">Ejecter</button>' : ""}
        </div>
      </div>
    `;
    applyRoleVisibility();
    const mountBtn = document.getElementById("cdrom-mount-btn");
    if (mountBtn) mountBtn.addEventListener("click", async () => {
      const iso = document.getElementById("cdrom-select").value;
      if (!iso) return;
      try {
        await api("PUT", `/vms/${encodeURIComponent(name)}/cdrom`, { iso });
        toast("ISO montee.", "success");
        loadVMCdromTab(name);
        loadVMDisksTab(name);
      } catch (e) { toast(e.message, "error"); }
    });
    const ejectBtn = document.getElementById("cdrom-eject-btn");
    if (ejectBtn) ejectBtn.addEventListener("click", async () => {
      try {
        await api("DELETE", `/vms/${encodeURIComponent(name)}/cdrom`);
        toast("ISO ejectee.", "success");
        loadVMCdromTab(name);
        loadVMDisksTab(name);
      } catch (e) { toast(e.message, "error"); }
    });
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function loadVMNetTab(name) {
  const el = document.getElementById("tab-net");
  try {
    const info = await api("GET", `/vms/${encodeURIComponent(name)}/network`);
    const networks = await api("GET", "/networks");
    const primary = info.interfaces[0] || {};
    const ifaceRows = info.interfaces.map((iface) => `
      <li>
        <span>${iface.reseau || "—"} <span style="font-size:11px;color:var(--grey)">${iface.mac || "—"}</span></span>
        ${info.interfaces.length > 1 ? `<button class="btn-small btn-danger admin-only" data-mac="${iface.mac}">Detacher</button>` : ""}
      </li>`).join("");
    const options = networks.map((n) => `<option value="${n.nom}" ${n.nom === primary.reseau ? "selected" : ""}>${n.nom} (${n.type})</option>`).join("");
    const addOptions = networks.map((n) => `<option value="${n.nom}">${n.nom} (${n.type})</option>`).join("");
    el.innerHTML = `
      <ul class="inline-list">${ifaceRows || "<li>Aucune interface.</li>"}</ul>
      <ul class="inline-list"><li><span>IP</span><span>${info.ip || "—"}</span></li></ul>
      <div class="form-row admin-only" style="margin-top:14px">
        <label>Changer le reseau principal</label>
        <div style="display:flex; gap:6px">
          <select id="net-select" style="flex:1">${options}</select>
          <button class="btn-secondary" id="net-apply-btn">Associer</button>
        </div>
      </div>
      <div class="form-row admin-only" style="margin-top:10px">
        <label>Ajouter une interface</label>
        <div style="display:flex; gap:6px">
          <select id="net-add-select" style="flex:1">${addOptions}</select>
          <button class="btn-secondary" id="net-add-btn">Ajouter</button>
        </div>
      </div>
    `;
    applyRoleVisibility();
    el.querySelectorAll("button[data-mac]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm("Detacher cette interface reseau ?")) return;
      try {
        await api("DELETE", `/vms/${encodeURIComponent(name)}/interfaces/${encodeURIComponent(btn.dataset.mac)}`);
        toast("Interface detachee.", "success");
        loadVMNetTab(name);
      } catch (e) { toast(e.message, "error"); }
    }));
    const applyBtn = document.getElementById("net-apply-btn");
    if (applyBtn) applyBtn.addEventListener("click", async () => {
      const net = document.getElementById("net-select").value;
      try {
        await api("PUT", `/vms/${encodeURIComponent(name)}/network`, { network: net });
        toast("VM associee au reseau '" + net + "'.", "success");
        loadVMNetTab(name);
      } catch (e) { toast(e.message, "error"); }
    });
    const addBtn = document.getElementById("net-add-btn");
    if (addBtn) addBtn.addEventListener("click", async () => {
      const net = document.getElementById("net-add-select").value;
      try {
        await api("POST", `/vms/${encodeURIComponent(name)}/interfaces`, { network: net });
        toast(`Interface ajoutee sur '${net}'.`, "success");
        loadVMNetTab(name);
      } catch (e) { toast(e.message, "error"); }
    });
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function loadVMSnapTab(name) {
  const el = document.getElementById("tab-snap");
  try {
    const snaps = await api("GET", `/vms/${encodeURIComponent(name)}/snapshots`);
    const rows = snaps.map((s) => `
      <li>
        <span>${s.nom}${s.actuel ? " (actuel)" : ""}${s.description ? " — " + s.description : ""}</span>
        <span>
          <button class="btn-small btn-secondary admin-only" data-restore="${s.nom}">Restaurer</button>
          <button class="btn-small btn-danger admin-only" data-delsnap="${s.nom}">Supprimer</button>
        </span>
      </li>`).join("");
    el.innerHTML = `
      <ul class="inline-list">${rows || "<li>Aucun snapshot.</li>"}</ul>
      <div class="form-row admin-only" style="margin-top:14px">
        <label>Nouveau snapshot</label>
        <div style="display:flex; gap:6px">
          <input type="text" id="snap-name" placeholder="nom" style="flex:1">
          <button class="btn-secondary" id="snap-create-btn">Creer</button>
        </div>
      </div>
    `;
    applyRoleVisibility();
    el.querySelectorAll("button[data-restore]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm(`Restaurer la VM a l'etat du snapshot '${btn.dataset.restore}' ? L'etat actuel sera perdu.`)) return;
      try {
        await api("POST", `/vms/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(btn.dataset.restore)}/restore?confirm=true`);
        toast("VM restauree.", "success");
        loadVMSnapTab(name); loadVMInfoTab(name);
      } catch (e) { toast(e.message, "error"); }
    }));
    el.querySelectorAll("button[data-delsnap]").forEach((btn) => btn.addEventListener("click", async () => {
      try {
        await api("DELETE", `/vms/${encodeURIComponent(name)}/snapshots/${encodeURIComponent(btn.dataset.delsnap)}`);
        toast("Snapshot supprime.", "success");
        loadVMSnapTab(name);
      } catch (e) { toast(e.message, "error"); }
    }));
    const createBtn = document.getElementById("snap-create-btn");
    if (createBtn) createBtn.addEventListener("click", async () => {
      const snapName = document.getElementById("snap-name").value.trim();
      if (!snapName) return;
      try {
        await api("POST", `/vms/${encodeURIComponent(name)}/snapshots`, { name: snapName });
        toast("Snapshot cree.", "success");
        loadVMSnapTab(name);
      } catch (e) { toast(e.message, "error"); }
    });
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

// ---------- Storage ----------
async function loadStorage() {
  const el = document.getElementById("storage-pools");
  el.innerHTML = "<p>Chargement...</p>";
  try {
    const pools = await api("GET", "/storage");
    if (!pools.length) { el.innerHTML = "<p>Aucun pool de stockage.</p>"; return; }
    el.innerHTML = "";
    for (const pool of pools) {
      const block = document.createElement("div");
      block.className = "pool-block";
      block.innerHTML = `
        <div class="pool-header">
          <div>
            <h3>${pool.nom}</h3>
            <div class="meta">${pool.etat} · ${pool.disponible_go} Go disponibles sur ${pool.capacite_go} Go</div>
          </div>
          <button class="btn-secondary admin-only" data-newvol="${pool.nom}">+ Volume</button>
        </div>
        <table class="data-table">
          <thead><tr><th>Nom</th><th>Taille</th><th>Utilise</th><th>Actions</th></tr></thead>
          <tbody id="vols-${pool.nom}"><tr class="empty-row"><td colspan="4">Chargement...</td></tr></tbody>
        </table>
      `;
      el.appendChild(block);
      loadVolumes(pool.nom);
    }
    applyRoleVisibility();
    el.querySelectorAll("button[data-newvol]").forEach((btn) => btn.addEventListener("click", () => openCreateVolumeModal(btn.dataset.newvol)));
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function loadVolumes(pool) {
  const tbody = document.getElementById("vols-" + pool);
  if (!tbody) return;
  try {
    const vols = await api("GET", `/storage/${encodeURIComponent(pool)}/volumes`);
    if (!vols.length) { tbody.innerHTML = `<tr class="empty-row"><td colspan="4">Aucun volume.</td></tr>`; return; }
    tbody.innerHTML = vols.map((v) => `
      <tr>
        <td>${v.nom}</td>
        <td>${v.capacite_go} Go</td>
        <td>${v.utilise ? '<span class="badge autre">oui</span>' : '<span class="badge actif">non</span>'}</td>
        <td><button class="btn-small btn-danger admin-only" data-delvol="${v.nom}" data-pool="${pool}" ${v.utilise ? "disabled" : ""}>Supprimer</button></td>
      </tr>`).join("");
    applyRoleVisibility();
    tbody.querySelectorAll("button[data-delvol]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm(`Supprimer le volume '${btn.dataset.delvol}' ?`)) return;
      try {
        await api("DELETE", `/storage/${encodeURIComponent(btn.dataset.pool)}/volumes/${encodeURIComponent(btn.dataset.delvol)}?confirm=true`);
        toast("Volume supprime.", "success");
        loadVolumes(btn.dataset.pool);
      } catch (e) { toast(e.message, "error"); }
    }));
  } catch (e) { tbody.innerHTML = `<tr class="empty-row"><td colspan="4">Erreur : ${e.message}</td></tr>`; }
}

function openCreateVolumeModal(pool) {
  openModal(`
    <button class="close-x" onclick="closeModal()">&times;</button>
    <h2>Nouveau volume — ${pool}</h2>
    <form id="create-vol-form">
      <div class="form-row"><label>Nom</label><input type="text" id="vol-name" required placeholder="ex. data-01"></div>
      <div class="form-row"><label>Taille (Go)</label><input type="number" id="vol-size" min="1" max="100" value="5" required></div>
      <p class="form-error" id="vol-error"></p>
      <div class="form-actions">
        <button type="button" class="btn-secondary" onclick="closeModal()">Annuler</button>
        <button type="submit" class="btn-primary">Creer</button>
      </div>
    </form>
  `);
  document.getElementById("create-vol-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("vol-error");
    try {
      await api("POST", `/storage/${encodeURIComponent(pool)}/volumes`, {
        name: document.getElementById("vol-name").value.trim(),
        size_gb: parseInt(document.getElementById("vol-size").value, 10),
      });
      toast("Volume cree.", "success");
      closeModal();
      loadVolumes(pool);
    } catch (err) { errEl.textContent = err.message; }
  });
}

// ---------- Networks ----------
async function loadNetworks() {
  const el = document.getElementById("networks-list");
  el.innerHTML = "<p>Chargement...</p>";
  try {
    const nets = await api("GET", "/networks");
    if (!nets.length) { el.innerHTML = "<p>Aucun reseau.</p>"; return; }
    el.innerHTML = nets.map((n) => `
      <div class="net-card" data-net="${n.nom}">
        <h3>${n.nom} <span class="badge ${n.type === 'nat' ? 'nat' : (n.type === 'isole' ? 'isole' : 'autre')}">${n.type}</span></h3>
        <div class="meta">Pont : ${n.pont || "—"} · ${n.actif ? "actif" : "inactif"}${n.reseau ? " · " + n.reseau.adresse + "/" + n.reseau.masque : ""}</div>
      </div>
    `).join("");
    el.querySelectorAll(".net-card").forEach((c) => c.addEventListener("click", () => openNetworkDetails(c.dataset.net)));
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function openNetworkDetails(name) {
  openModal(`<button class="close-x" onclick="closeModal()">&times;</button><h2>${name}</h2><p>Chargement...</p>`);
  try {
    const n = await api("GET", `/networks/${encodeURIComponent(name)}`);
    const leases = (n.baux_dhcp || []).map((l) => `<li><span>${l.hostname || "?"}</span><span>${l.ip} (${l.mac})</span></li>`).join("");
    document.getElementById("modal-content").innerHTML = `
      <button class="close-x" onclick="closeModal()">&times;</button>
      <h2>${n.nom}</h2>
      <ul class="inline-list">
        <li><span>Type</span><span class="badge ${n.type === 'nat' ? 'nat' : (n.type === 'isole' ? 'isole' : 'autre')}">${n.type}</span></li>
        <li><span>Pont</span><span>${n.pont || "—"}</span></li>
        <li><span>Etat</span><span>${n.actif ? "actif" : "inactif"}</span></li>
        <li><span>Sous-reseau</span><span>${n.reseau ? n.reseau.adresse + " / " + n.reseau.masque : "—"}</span></li>
      </ul>
      <h3 style="font-size:14px;color:var(--navy);margin-top:16px">Baux DHCP actifs</h3>
      <ul class="inline-list">${leases || "<li>Aucun.</li>"}</ul>
    `;
  } catch (e) {
    document.getElementById("modal-content").innerHTML = `<button class="close-x" onclick="closeModal()">&times;</button><p class="error">${e.message}</p>`;
  }
}

// ---------- Templates ----------
async function loadTemplates() {
  const tbody = document.getElementById("templates-tbody");
  tbody.innerHTML = `<tr class="empty-row"><td colspan="6">Chargement...</td></tr>`;
  try {
    const tpls = await api("GET", "/templates");
    if (!tpls.length) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="6">Aucun template. Convertissez une VM arretee depuis l'onglet "Machines virtuelles".</td></tr>`;
      return;
    }
    tbody.innerHTML = tpls.map((t) => `
      <tr>
        <td>${t.nom}</td>
        <td>${t.vm_source}</td>
        <td>${t.vcpu}</td>
        <td>${Math.round(t.memoire_mo)} Mo</td>
        <td>${t.cree_le || "—"}</td>
        <td>
          <button class="btn-small btn-secondary admin-only" data-deploy="${t.nom}">Deployer</button>
          <button class="btn-small btn-danger admin-only" data-deltpl="${t.nom}">Supprimer</button>
        </td>
      </tr>
    `).join("");
    applyRoleVisibility();
    tbody.querySelectorAll("button[data-deploy]").forEach((btn) => btn.addEventListener("click", async () => {
      const tplName = btn.dataset.deploy;
      const newName = prompt(`Nom de la nouvelle VM a deployer depuis le template '${tplName}' :`, `${tplName}-01`);
      if (!newName || !newName.trim()) return;
      try {
        await api("POST", `/templates/${encodeURIComponent(tplName)}/deploy`, { new_name: newName.trim() });
        toast(`VM '${newName.trim()}' deployee depuis le template '${tplName}' (arretee).`, "success");
        loadDashboard();
      } catch (e) { toast(e.message, "error"); }
    }));
    tbody.querySelectorAll("button[data-deltpl]").forEach((btn) => btn.addEventListener("click", async () => {
      const tplName = btn.dataset.deltpl;
      if (!confirm(`Supprimer definitivement le template '${tplName}' et son disque ?`)) return;
      try {
        await api("DELETE", `/templates/${encodeURIComponent(tplName)}?confirm=true`);
        toast(`Template '${tplName}' supprime.`, "success");
        loadTemplates();
      } catch (e) { toast(e.message, "error"); }
    }));
  } catch (e) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="6">Erreur : ${e.message}</td></tr>`;
  }
}

// ---------- ISO ----------
async function loadIsos() {
  const tbody = document.getElementById("isos-tbody");
  tbody.innerHTML = `<tr class="empty-row"><td colspan="3">Chargement...</td></tr>`;
  try {
    const isos = await api("GET", "/isos");
    if (!isos.length) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="3">Aucune image ISO.</td></tr>`;
      return;
    }
    tbody.innerHTML = isos.map((i) => `
      <tr>
        <td>${i.nom}</td>
        <td>${i.taille_mo} Mo</td>
        <td><button class="btn-small btn-danger admin-only" data-deliso="${i.nom}">Supprimer</button></td>
      </tr>
    `).join("");
    applyRoleVisibility();
    tbody.querySelectorAll("button[data-deliso]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm(`Supprimer l'ISO '${btn.dataset.deliso}' ?`)) return;
      try {
        await api("DELETE", `/isos/${encodeURIComponent(btn.dataset.deliso)}?confirm=true`);
        toast("ISO supprimee.", "success");
        loadIsos();
      } catch (e) { toast(e.message, "error"); }
    }));
  } catch (e) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="3">Erreur : ${e.message}</td></tr>`;
  }
}

const isoUploadForm = document.getElementById("iso-upload-form");
if (isoUploadForm) isoUploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("iso-file-input");
  const errEl = document.getElementById("iso-upload-error");
  const progressWrap = document.getElementById("iso-upload-progress");
  const progressBar = document.getElementById("iso-upload-progress-bar");
  const progressLabel = document.getElementById("iso-upload-progress-label");
  const submitBtn = isoUploadForm.querySelector("button[type=submit]");
  errEl.textContent = "";
  if (!fileInput.files.length) return;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  submitBtn.disabled = true;
  progressBar.style.width = "0%";
  progressLabel.textContent = "0%";
  progressWrap.style.display = "block";
  try {
    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/isos");
      xhr.setRequestHeader("Authorization", "Bearer " + state.token);
      xhr.upload.addEventListener("progress", (ev) => {
        if (!ev.lengthComputable) return;
        const pct = Math.round((ev.loaded / ev.total) * 100);
        progressBar.style.width = pct + "%";
        progressLabel.textContent = pct + "%";
      });
      xhr.addEventListener("load", () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e2) { data = null; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error((data && data.detail) || `Erreur televersement (HTTP ${xhr.status})`));
      });
      xhr.addEventListener("error", () => reject(new Error("Erreur reseau pendant le televersement")));
      xhr.send(fd);
    });
    toast("ISO televersee.", "success");
    fileInput.value = "";
    loadIsos();
  } catch (err) {
    errEl.textContent = err.message;
  } finally {
    submitBtn.disabled = false;
    progressWrap.style.display = "none";
  }
});

// ---------- Console VNC ----------
let currentRFB = null;

async function openConsole(name) {
  try {
    const ticketResp = await api("POST", `/vms/${encodeURIComponent(name)}/console-ticket`);
    const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${wsProto}://${window.location.host}/vms/${encodeURIComponent(name)}/console?ticket=${encodeURIComponent(ticketResp.ticket)}`;

    document.getElementById("console-title").textContent = "Console — " + name;
    document.getElementById("console-screen").innerHTML = "";
    document.getElementById("console-overlay").style.display = "flex";

    const mod = await import("/static/novnc/core/rfb.js");
    const RFB = mod.default;
    currentRFB = new RFB(document.getElementById("console-screen"), wsUrl);
    currentRFB.addEventListener("disconnect", () => {
      toast(`Console fermee pour '${name}'.`);
    });
    currentRFB.addEventListener("credentialsrequired", () => {
      toast("Cette VM demande des identifiants VNC non geres par Hyperlite.", "error");
      closeConsole();
    });
  } catch (e) {
    toast(e.message, "error");
    closeConsole();
  }
}

function closeConsole() {
  if (currentRFB) {
    try { currentRFB.disconnect(); } catch (e) { /* ignore */ }
    currentRFB = null;
  }
  const overlay = document.getElementById("console-overlay");
  if (overlay) overlay.style.display = "none";
  const screen = document.getElementById("console-screen");
  if (screen) screen.innerHTML = "";
}

const consoleCloseBtn = document.getElementById("console-close-btn");
if (consoleCloseBtn) consoleCloseBtn.addEventListener("click", closeConsole);

// ---------- Terminal SSH ----------
let xtermAssetsLoaded = false;
function ensureXtermLoaded() {
  if (xtermAssetsLoaded) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/xterm/xterm.css";
    document.head.appendChild(link);
    const s1 = document.createElement("script");
    s1.src = "/static/xterm/xterm.js";
    s1.onload = () => {
      const s2 = document.createElement("script");
      s2.src = "/static/xterm/addon-fit.js";
      s2.onload = () => { xtermAssetsLoaded = true; resolve(); };
      s2.onerror = () => reject(new Error("Impossible de charger l'addon de redimensionnement du terminal."));
      document.head.appendChild(s2);
    };
    s1.onerror = () => reject(new Error("Impossible de charger xterm.js."));
    document.head.appendChild(s1);
  });
}

let currentTerm = null;
let currentTermWs = null;
let currentTermResizeHandler = null;

async function openTerminal(name) {
  try {
    await ensureXtermLoaded();
    const ticketResp = await api("POST", `/vms/${encodeURIComponent(name)}/terminal-ticket`);
    const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${wsProto}://${window.location.host}/vms/${encodeURIComponent(name)}/terminal?ticket=${encodeURIComponent(ticketResp.ticket)}`;

    document.getElementById("terminal-title").textContent = `Terminal — ${name} (${ticketResp.utilisateur})`;
    const screen = document.getElementById("terminal-screen");
    screen.innerHTML = "";
    document.getElementById("terminal-overlay").style.display = "flex";

    const term = new Terminal({ cursorBlink: true, fontSize: 14, theme: { background: "#000000" } });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(screen);
    fitAddon.fit();
    currentTerm = term;

    const ws = new WebSocket(wsUrl);
    currentTermWs = ws;
    ws.onopen = () => {
      fitAddon.fit();
      ws.send("\x00" + JSON.stringify({ cols: term.cols, rows: term.rows }));
    };
    ws.onmessage = (ev) => term.write(ev.data);
    ws.onclose = () => term.write("\r\n\x1b[33m[connexion terminee]\x1b[0m\r\n");
    ws.onerror = () => toast("Erreur de connexion au terminal.", "error");

    term.onData((data) => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
    term.onResize(({ cols, rows }) => { if (ws.readyState === WebSocket.OPEN) ws.send("\x00" + JSON.stringify({ cols, rows })); });

    currentTermResizeHandler = () => fitAddon.fit();
    window.addEventListener("resize", currentTermResizeHandler);
  } catch (e) {
    toast(e.message, "error");
    closeTerminal();
  }
}

function closeTerminal() {
  if (currentTermWs) {
    try { currentTermWs.close(); } catch (e) { /* ignore */ }
    currentTermWs = null;
  }
  if (currentTerm) {
    try { currentTerm.dispose(); } catch (e) { /* ignore */ }
    currentTerm = null;
  }
  if (currentTermResizeHandler) {
    window.removeEventListener("resize", currentTermResizeHandler);
    currentTermResizeHandler = null;
  }
  const overlay = document.getElementById("terminal-overlay");
  if (overlay) overlay.style.display = "none";
  const screen = document.getElementById("terminal-screen");
  if (screen) screen.innerHTML = "";
}

const terminalCloseBtn = document.getElementById("terminal-close-btn");
if (terminalCloseBtn) terminalCloseBtn.addEventListener("click", closeTerminal);

// ---------- Init ----------
tryRestoreSession();
