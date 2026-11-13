import { create } from "zustand";
import {
  Activity, BellRing, Box, Braces, BrickWall, Camera, ChartColumn, Database, DatabaseBackup, Disc3, Gauge, Globe, HeartPulse, House, KeyRound,
  Layers, LayoutGrid, Lightbulb, ListChecks, Monitor, Plus, Network, PackageOpen, Scale, ScrollText, Server, ShipWheel, Users, Waypoints, Workflow,
} from "lucide-react";

// The services of the console: one per datacenter page, under its historical `?tab=` id so every existing link
// keeps working. `cat` is the family that colours its tile; `admin` pages are reserved to administrators by the
// backend, `apiDocs` follows the API documentation setting.
export const CATEGORIES = ["compute", "storage", "network", "cluster", "protect", "monitor", "auto", "identity"];
export const SERVICES = [
  { id: "vms", icon: Monitor, cat: "compute", label: "nav.vms" },
  { id: "containers", icon: Box, cat: "compute" },
  { id: "kubernetes", icon: ShipWheel, cat: "compute" },
  { id: "nodes", icon: Server, cat: "compute" },
  { id: "storage", icon: Database, cat: "storage" },
  { id: "library", icon: Disc3, cat: "storage" },
  { id: "network", icon: Network, cat: "network" },
  { id: "firewall", icon: BrickWall, cat: "network", admin: true },
  { id: "sites", icon: Globe, cat: "network", admin: true },
  { id: "ha", icon: HeartPulse, cat: "cluster" },
  { id: "drs", icon: Scale, cat: "cluster" },
  { id: "compat", icon: ListChecks, cat: "cluster" },
  { id: "cfs", icon: Waypoints, cat: "cluster", admin: true },
  { id: "backups", icon: DatabaseBackup, cat: "protect" },
  { id: "snapshots", icon: Camera, cat: "protect" },
  { id: "exports", icon: PackageOpen, cat: "protect", admin: true },
  { id: "advisor", icon: Lightbulb, cat: "monitor" },
  { id: "perf", icon: Gauge, cat: "monitor" },
  { id: "activity", icon: Activity, cat: "monitor", label: "nav.activity" },
  { id: "journal", icon: ScrollText, cat: "monitor", admin: true },
  { id: "metrics", icon: ChartColumn, cat: "monitor", admin: true },
  { id: "notifications", icon: BellRing, cat: "monitor", admin: true },
  { id: "automation", icon: Workflow, cat: "auto" },
  { id: "api", icon: Braces, cat: "auto", apiDocs: true },
  { id: "permissions", icon: Users, cat: "identity", admin: true, label: "nav.usersRoles" },
  { id: "sso", icon: KeyRound, cat: "identity", admin: true, label: "svc.sso" },
];
// The pages of the console itself: they are not services and never appear in the catalogue.
const BASE = [
  { id: "summary", icon: House, cat: "base", label: "nav.overview" },
  { id: "all", icon: Layers, cat: "base", label: "svc.all" },
  { id: "services", icon: LayoutGrid, cat: "base", label: "svc.services" },
  { id: "create", icon: Plus, cat: "base", label: "svc.create" },
];
export const SERVICE = Object.fromEntries([...SERVICES, ...BASE].map((s) => [s.id, s]));
export const serviceLabel = (id) => SERVICE[id]?.label || `tab.${id}`;

// Historical ids that open the same page as a service.
const ALIASES = { templates: "library" };

// The services this person may open.
export function allowedServices({ admin = false, apiDocs = false } = {}) {
  return SERVICES.filter((s) => (s.admin ? admin : s.apiDocs ? admin || apiDocs : true));
}

// The menu entry that stands for what is shown: an object page belongs to the list of its kind.
export function activeService(selection, tab) {
  if (selection?.type === "node") return "nodes";
  if (selection?.type === "vm") return "vms";
  if (selection?.type === "container") return "containers";
  return ALIASES[tab] || tab || "summary";
}

// The menu of a resource page: its pages in groups, an overview first. `admin` pages are hidden from the others.
export const RESOURCE_MENUS = {
  vm: [
    { pages: ["summary", "tasks", { id: "permissions", admin: true }] },
    { group: "res.g.connect", pages: ["console"] },
    { group: "res.g.settings", pages: ["hardware", "advanced", "options", "network", "cloudinit"] },
    { group: "res.g.protection", pages: ["snapshots", "backup"] },
    { group: "res.g.monitoring", pages: ["perf", "advisor"] },
  ],
  node: [
    { pages: ["summary", "tasks"] },
    { group: "res.g.settings", pages: ["system", { id: "updates", admin: true }, "network", "disk"] },
    { group: "res.g.monitoring", pages: ["perf", "compat", "advisor"] },
    { group: "res.g.connect", pages: ["shell"] },
  ],
  container: [
    { pages: ["summary", "tasks", { id: "permissions", admin: true }] },
    { group: "res.g.connect", pages: ["console"] },
    { group: "res.g.protection", pages: ["snapshots", "backup"] },
  ],
};
const PAGE_LABELS = { summary: "res.overview", tasks: "tab.tasks", permissions: "tab.objPermissions", backup: "tab.backups" };
export const resourcePageLabel = (id) => PAGE_LABELS[id] || `tab.${id}`;
export const LIST_OF = { vm: "vms", node: "nodes", container: "containers" };

// Favourites (the services pinned in the left menu) and recently opened resources: kept in this browser.
const FAV_KEY = "hyperlite-console-favorites";
const RECENT_KEY = "hyperlite-console-recent";
export const DEFAULT_FAVORITES = ["vms", "containers", "nodes", "storage", "network", "backups", "perf", "activity", "permissions"];
function read(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key)); return Array.isArray(v) ? v : fallback; } catch { return fallback; }
}
function write(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* kept for this session only */ } }

export const useConsoleStore = create((set, get) => ({
  favorites: read(FAV_KEY, DEFAULT_FAVORITES).filter((id) => SERVICE[id]),
  recent: read(RECENT_KEY, []),
  toggleFavorite(id) {
    const cur = get().favorites;
    const favorites = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    write(FAV_KEY, favorites);
    set({ favorites });
  },
  // Remembers that a resource was opened: the most recent first, each resource once, ten at most.
  visit(type, id, name) {
    if (!type || id == null) return;
    const first = get().recent[0];
    if (first && first.type === type && first.id === id) return;
    const recent = [{ type, id, name: name || String(id), at: Date.now() }, ...get().recent.filter((r) => !(r.type === type && r.id === id))].slice(0, 10);
    write(RECENT_KEY, recent);
    set({ recent });
  },
}));
