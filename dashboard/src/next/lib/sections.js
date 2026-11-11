// The sections of the rail: each one owns a set of datacenter pages (the historical `?tab=` ids). The inventory
// section also owns every node, VM and container page. A page may only appear in one section.
export const SECTIONS = [
  { id: "inventory", label: "rail.inventory", pages: ["summary", "nodes", "vms", "perf", "ha", "drs", "compat", "containers", "kubernetes", "library", "templates"] },
  { id: "storage", label: "rail.storage", pages: ["storage"] },
  { id: "network", label: "rail.network", pages: ["network", { id: "firewall", admin: true }] },
  { id: "protection", label: "rail.protection", pages: ["backups", "snapshots", "exports", { id: "sites", admin: true }] },
  { id: "monitoring", label: "rail.monitoring", pages: ["activity", { id: "journal", admin: true }, { id: "metrics", admin: true }, { id: "notifications", admin: true }] },
  { id: "automation", label: "rail.automation", pages: ["automation", { id: "api", apiDocs: true }] },
  { id: "admin", label: "rail.admin", admin: true, pages: [{ id: "permissions", admin: true }, { id: "sso", admin: true }, { id: "cfs", admin: true }] },
];

// The cluster's own tabs, shown under its header: the pages that describe the whole cluster.
export const CLUSTER_TABS = ["summary", "nodes", "vms", "perf", "ha", "drs", "compat"];

const pageId = (p) => (typeof p === "string" ? p : p.id);

// The pages of a section that this person may open.
export function sectionPages(section, { admin = false, apiDocs = false } = {}) {
  const allowed = (p) => typeof p === "string" || (p.admin ? admin : p.apiDocs ? admin || apiDocs : true);
  return section.pages.filter(allowed).map(pageId);
}

// The section a selection belongs to: an object (node, VM, container) is always in the inventory.
export function sectionOf(selection, tab) {
  if (selection?.type && selection.type !== "datacenter") return "inventory";
  const page = tab || "summary";
  return (SECTIONS.find((s) => s.pages.some((p) => pageId(p) === page)) || SECTIONS[0]).id;
}
