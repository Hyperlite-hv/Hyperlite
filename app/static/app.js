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
          <button class="btn-small btn-secondary admin-only" data-action="start" data-name="${vm.nom}" ${vm.etat === "actif" ? "disabled" : ""}>Demarrer</button>
          <button class="btn-small btn-secondary admin-only" data-action="stop" data-name="${vm.nom}" ${vm.etat !== "actif" ? "disabled" : ""}>Arreter</button>
          <button class="btn-small btn-secondary admin-only" data-action="restart" data-name="${vm.nom}" ${vm.etat !== "actif" ? "disabled" : ""}>Redemarrer</button>
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

async function handleVMAction(action, name) {
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
  const options = networks.map((n) => `<option value="${n.nom}" ${n.nom === "default" ? "selected" : ""}>${n.nom} (${n.type})</option>`).join("");
  openModal(`
    <button class="close-x" onclick="closeModal()">&times;</button>
    <h2>Creer une VM</h2>
    <form id="create-vm-form">
      <div class="form-row"><label>Nom</label><input type="text" id="cv-name" required pattern="[a-zA-Z0-9][a-zA-Z0-9\\-]{1,62}" placeholder="ex. web-01"></div>
      <div class="form-row"><label>vCPU (1-2)</label><input type="number" id="cv-vcpu" min="1" max="2" value="1" required></div>
      <div class="form-row"><label>Memoire (Mo, 256-2048)</label><input type="number" id="cv-mem" min="256" max="2048" value="768" required></div>
      <div class="form-row"><label>Disque (Go, 1-20)</label><input type="number" id="cv-disk" min="1" max="20" value="5" required></div>
      <div class="form-row"><label>Reseau</label><select id="cv-network">${options || '<option value="default">default</option>'}</select></div>
      <div class="form-row"><label>Mot de passe (utilisateur hyperlite)</label><input type="text" id="cv-password" placeholder="laisser vide = 'hyperlite'"></div>
      <p class="form-error" id="cv-error"></p>
      <div class="form-actions">
        <button type="button" class="btn-secondary" onclick="closeModal()">Annuler</button>
        <button type="submit" class="btn-primary">Creer</button>
      </div>
    </form>
  `);
  document.getElementById("create-vm-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("cv-error");
    errEl.textContent = "";
    const payload = {
      name: document.getElementById("cv-name").value.trim(),
      vcpu: parseInt(document.getElementById("cv-vcpu").value, 10),
      memory_mb: parseInt(document.getElementById("cv-mem").value, 10),
      disk_gb: parseInt(document.getElementById("cv-disk").value, 10),
      network: document.getElementById("cv-network").value || "default",
    };
    const pwd = document.getElementById("cv-password").value;
    if (pwd) payload.password = pwd;
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
      <button class="tab-btn" data-tab="net">Reseau</button>
      <button class="tab-btn" data-tab="snap">Snapshots</button>
    </div>
    <div class="tab-pane active" id="tab-info"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-disks"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-net"><p>Chargement...</p></div>
    <div class="tab-pane" id="tab-snap"><p>Chargement...</p></div>
  `);
  document.querySelectorAll(".tab-btn").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === "tab-" + btn.dataset.tab));
  }));

  loadVMInfoTab(name);
  loadVMDisksTab(name);
  loadVMNetTab(name);
  loadVMSnapTab(name);
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
        <li><span>UUID</span><span style="font-size:11px">${vm.uuid}</span></li>
      </ul>
    `;
  } catch (e) { el.innerHTML = `<p class="error">${e.message}</p>`; }
}

async function loadVMDisksTab(name) {
  const el = document.getElementById("tab-disks");
  try {
    const disks = await api("GET", `/vms/${encodeURIComponent(name)}/disks`);
    const rows = disks.map((d) => `
      <li>
        <span>${d.cible} (${d.bus || "?"}) ${d.type === "cdrom" ? "— cloud-init" : ""}</span>
        ${d.type !== "cdrom" && d.cible !== "vda" ? `<button class="btn-small btn-danger admin-only" data-dev="${d.cible}">Detacher</button>` : ""}
      </li>`).join("");
    el.innerHTML = `
      <ul class="inline-list">${rows || "<li>Aucun disque.</li>"}</ul>
      <div class="form-row admin-only" style="margin-top:14px">
        <label>Attacher un volume existant</label>
        <div style="display:flex; gap:6px">
          <input type="text" id="attach-vol-name" placeholder="nom-du-volume.qcow2" style="flex:1">
          <input type="text" id="attach-vol-dev" placeholder="vdb" style="width:80px">
          <button class="btn-secondary" id="attach-vol-btn">Attacher</button>
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
    const attachBtn = document.getElementById("attach-vol-btn");
    if (attachBtn) attachBtn.addEventListener("click", async () => {
      const volName = document.getElementById("attach-vol-name").value.trim();
      const dev = document.getElementById("attach-vol-dev").value.trim() || "vdb";
      if (!volName) return;
      try {
        await api("POST", `/vms/${encodeURIComponent(name)}/disks`, { volume_name: volName, pool: "default", target_dev: dev });
        toast("Volume attache.", "success");
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
    const iface = info.interfaces[0] || {};
    const options = networks.map((n) => `<option value="${n.nom}" ${n.nom === iface.reseau ? "selected" : ""}>${n.nom} (${n.type})</option>`).join("");
    el.innerHTML = `
      <ul class="inline-list">
        <li><span>Reseau actuel</span><span>${iface.reseau || "—"}</span></li>
        <li><span>MAC</span><span>${iface.mac || "—"}</span></li>
        <li><span>IP</span><span>${info.ip || "—"}</span></li>
      </ul>
      <div class="form-row admin-only" style="margin-top:14px">
        <label>Changer de reseau</label>
        <div style="display:flex; gap:6px">
          <select id="net-select" style="flex:1">${options}</select>
          <button class="btn-secondary" id="net-apply-btn">Associer</button>
        </div>
      </div>
    `;
    applyRoleVisibility();
    const applyBtn = document.getElementById("net-apply-btn");
    if (applyBtn) applyBtn.addEventListener("click", async () => {
      const net = document.getElementById("net-select").value;
      try {
        await api("PUT", `/vms/${encodeURIComponent(name)}/network`, { network: net });
        toast("VM associee au reseau '" + net + "'.", "success");
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

// ---------- Init ----------
tryRestoreSession();
