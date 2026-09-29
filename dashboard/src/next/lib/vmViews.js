// VM list views: how the list is grouped, which optional columns it shows, and the views a person saved (a name
// for a set of filters, grouping, columns and sort). Kept in the browser, like the other list preferences.
const GROUP_KEY = "hyperlite-next-vmgroup";
const COLS_KEY = "hyperlite-next-vmcols";
const VIEWS_KEY = "hyperlite-next-vmviews";

export const GROUP_MODES = ["node", "tag", "pool", "none"];
export const OPTIONAL_COLUMNS = ["node", "ip", "os", "res", "uptime"];
export const DEFAULT_COLUMNS = ["node", "ip", "res", "uptime"];

function read(key) { try { return localStorage.getItem(key); } catch { return null; } }
function write(key, value) { try { localStorage.setItem(key, value); } catch { /* preference only */ } }

// Grouping by node is the default; the historical "1"/"0" values of the node toggle are read as node/none.
export function readGroupMode() {
  const v = read(GROUP_KEY);
  if (v === "1" || v == null) return "node";
  if (v === "0") return "none";
  return GROUP_MODES.includes(v) ? v : "node";
}
export const saveGroupMode = (mode) => write(GROUP_KEY, mode);

export function readColumns() {
  try {
    const v = JSON.parse(read(COLS_KEY) || "null");
    return Array.isArray(v) ? OPTIONAL_COLUMNS.filter((c) => v.includes(c)) : DEFAULT_COLUMNS;
  } catch { return DEFAULT_COLUMNS; }
}
export const saveColumns = (cols) => write(COLS_KEY, JSON.stringify(cols));

const VIEW_FIELDS = { chip: "string", nodeFilter: "string", tagFilter: "string", q: "string", groupMode: "string" };
function cleanView(v) {
  if (!v || typeof v.name !== "string" || !v.name.trim()) return null;
  const state = {};
  for (const [k, type] of Object.entries(VIEW_FIELDS)) if (typeof v.state?.[k] === type) state[k] = v.state[k];
  if (Array.isArray(v.state?.columns)) state.columns = OPTIONAL_COLUMNS.filter((c) => v.state.columns.includes(c));
  if (v.state?.sort && typeof v.state.sort.key === "string" && [1, -1].includes(v.state.sort.dir)) state.sort = { key: v.state.sort.key, dir: v.state.sort.dir };
  if (state.groupMode && !GROUP_MODES.includes(state.groupMode)) delete state.groupMode;
  return { name: v.name.trim().slice(0, 40), state };
}
export function readSavedViews() {
  try {
    const v = JSON.parse(read(VIEWS_KEY) || "[]");
    return Array.isArray(v) ? v.map(cleanView).filter(Boolean) : [];
  } catch { return []; }
}
// Saving a view under an existing name replaces it.
export function saveView(views, name, state) {
  const view = cleanView({ name, state });
  if (!view) return views;
  const next = [...views.filter((v) => v.name !== view.name), view].sort((a, b) => a.name.localeCompare(b.name));
  write(VIEWS_KEY, JSON.stringify(next));
  return next;
}
export function deleteView(views, name) {
  const next = views.filter((v) => v.name !== name);
  write(VIEWS_KEY, JSON.stringify(next));
  return next;
}

// Groups of the shown VMs. By tag, a VM with several tags is listed under each of them and a VM without tag goes
// to a last group; by pool likewise with the pools it belongs to. Group order: nodes as given, tags and pools by
// name. `withEmpty` keeps the nodes without VM (nothing filtered: every machine appears).
export function groupVms(shown, mode, { nodes = [], tagsOf = () => [], pools = [], withEmpty = false, noneLabel = "" } = {}) {
  if (mode === "node") {
    const groups = nodes.map((n) => ({ key: `node:${n.id}`, kind: "node", node: n, label: n.nom, vms: shown.filter((v) => v.node === n.id) }))
      .filter((g) => g.vms.length > 0 || withEmpty);
    const orphans = shown.filter((v) => !nodes.some((n) => n.id === v.node));
    if (orphans.length) groups.push({ key: "node:?", kind: "node", node: { id: "?", nom: noneLabel }, label: noneLabel, vms: orphans });
    return groups;
  }
  if (mode === "tag" || mode === "pool") {
    const keysOf = mode === "tag" ? tagsOf : (v) => pools.filter((p) => (p.vms || []).includes(v.nom) && (!v.node || v.node === "local")).map((p) => p.name);
    const byKey = new Map();
    const loose = [];
    for (const v of shown) {
      const keys = keysOf(v);
      if (!keys.length) loose.push(v);
      for (const k of keys) byKey.set(k, [...(byKey.get(k) || []), v]);
    }
    const groups = [...byKey.keys()].sort((a, b) => a.localeCompare(b)).map((k) => ({ key: `${mode}:${k}`, kind: mode, label: k, vms: byKey.get(k) }));
    if (loose.length) groups.push({ key: `${mode}:`, kind: mode, label: noneLabel, none: true, vms: loose });
    return groups;
  }
  return null;
}
