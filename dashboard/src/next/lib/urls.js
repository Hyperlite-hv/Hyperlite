// Canonical URLs are the historical ones (`/datacenter`, `/node/:id`, `/vm/:id`) so every
// existing link keeps working; `?tab=` carries the tab exactly as before.
export function selectionToPath(sel) {
  if (!sel || sel.type === "datacenter") return "/datacenter";
  if (sel.type === "storage") return null; // no dedicated route (legacy behaviour)
  if (sel.type === "container") return null; // no page yet: opened through Virtual Machines > Containers
  return `/${sel.type}/${encodeURIComponent(sel.id)}`;
}
export function withTab(path, tab) {
  return tab && tab !== "summary" ? `${path}?tab=${encodeURIComponent(tab)}` : path;
}
