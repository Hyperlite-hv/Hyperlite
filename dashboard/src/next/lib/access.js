import { create } from "zustand";
import { fetchMyAccess } from "../../api/client";

// What the signed-in account may do where this node answers (GET /access/me): its area privileges and its
// assignments per perimeter (docs/design/access-2.md). The backend stays the authority; this only decides which
// actions and tabs the interface offers, so an account given rights by a delegate sees them without being an admin.
export const useAccess = create((set) => ({
  me: null,
  async load() {
    try { set({ me: await fetchMyAccess() }); } catch { set({ me: null }); }
  },
}));

export function canArea(me, privilege) {
  return !!me && (me.admin || (me.areas || []).includes(privilege));
}

// Holds a privilege through one of its assignments, anywhere: decides whether the Rights tabs are offered.
export function holdsAnywhere(me, privilege) {
  return !!me && (me.admin || (me.perimeters || []).some((p) => p.privileges.includes(privilege)));
}

// The perimeters an assignment can be given on, widest first (app/core/permissions.py, perimeters_of).
export const PERIMETER_KINDS = ["cluster", "node", "pool", "vm", "container", "storage", "network", "backup_store"];
