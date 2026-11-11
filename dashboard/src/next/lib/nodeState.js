const pct = (u, tot) => (u != null && tot ? (u / tot) * 100 : null);

// How a node reads at a glance: offline first, then maintenance, then short of memory or CPU, else online.
// `key` is the i18n key of the word shown with the colour (never colour alone).
export function nodeTone(n) {
  if (n.state !== "online") return { tone: "danger", key: "tree.state.offline" };
  if (n.maintenance) return { tone: "info", key: "tree.state.maintenance" };
  if ((pct(n.used_memory_mb, n.memoire_totale_mo) ?? 0) >= 80) return { tone: "warning", key: "tree.state.memory" };
  if ((n.cpu_utilisation ?? 0) >= 90) return { tone: "warning", key: "tree.state.cpu" };
  return { tone: "success", key: "tree.state.online" };
}
