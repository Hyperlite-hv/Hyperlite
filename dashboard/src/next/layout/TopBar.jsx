import { useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Bell, Box, ChevronDown, Database, LoaderCircle, Monitor, Network, PanelLeft, Plus, Search, Users } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { requestIntent } from "../lib/intents";
import { deriveAlerts, summarizeHealth } from "../lib/alerts";
import { useFreshness } from "../lib/inventory";
import { CLUSTER_TABS } from "../lib/sections";
import Menu, { MenuItem } from "../components/Menu";
import CreateVmWizard from "../wizard/CreateVmWizard";
import CreateContainerWizard from "../wizard/CreateContainerWizard";
import { useEnvironmentLabel } from "../lib/environment";
import { parseVmKey, findVm } from "../lib/vmId";

// The operations running now: the cluster's tasks, plus the ones this tab started that the server has not listed yet.
export function useRunningOps() {
  const running = useFreshness((s) => s.running);
  const local = useInfraStore((s) => s.tasks);
  return useMemo(() => {
    const seen = new Set(running.map((r) => `${r.type}:${r.target}`));
    const mine = local.filter((x) => x.status === "running" && !seen.has(`${x.type}:${x.target}`));
    return [...running, ...mine];
  }, [running, local]);
}

// Top bar: where you are (cluster, node, object), go-to search, the running operations, the alerts and Create.
export default function TopBar({ onOpenPalette, onToggleNavigator, wizards, setWizards }) {
  const t = useT();
  const { nodes, vms, storagePools, tasks, selection, activeTab, navigateTo } = useInfraStore(useShallow((s) => ({
    nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, tasks: s.tasks, selection: s.selection, activeTab: s.activeTab, navigateTo: s.navigateTo,
  })));
  const cluster = useFreshness((s) => s.cluster);
  const caps = capabilities(useAuthStore((s) => s.role));
  const [createOpen, setCreateOpen] = useState(false);
  const createBtn = useRef(null);
  const env = useEnvironmentLabel();
  const ops = useRunningOps();
  const health = summarizeHealth(useMemo(() => deriveAlerts({ nodes, vms, storagePools, tasks }), [nodes, vms, storagePools, tasks]));

  const rootLabel = cluster?.in_cluster ? cluster.name : t("crumb.datacenter");
  const page = activeTab || "summary";
  const crumbs = [];
  if (selection.type === "node") crumbs.push([nodes.find((n) => n.id === selection.id)?.name || selection.id]);
  else if (selection.type === "vm") {
    const vm = findVm(vms, selection.id);
    const node = vm && nodes.find((n) => n.id === (vm.node || "local"));
    if (node) crumbs.push([node.name, () => navigateTo("node", node.id, "summary")]);
    crumbs.push([parseVmKey(selection.id).name]);
  } else if (selection.type === "container") crumbs.push([t("nav.containers"), () => navigateTo("datacenter", null, "containers")], [selection.id]);
  else if (page !== "summary") crumbs.push([t(CLUSTER_TABS.includes(page) ? `ctab.${page}` : `tab.${page}`)]);

  const create = (kind) => {
    setCreateOpen(false);
    if (kind === "vm" || kind === "container") { setWizards({ [kind]: true }); return; }
    requestIntent(kind);
    navigateTo("datacenter", null, kind === "pool" ? "storage" : kind === "network" ? "network" : "permissions");
  };

  return (
    <header className={`nx-top${env ? " nx-top--env" : ""}`}>
      <button type="button" className="nx-btn nx-btn--icon nx-btn--ghost nx-top-navbtn" aria-label={t("nav.toggleSidebar")} onClick={onToggleNavigator}>
        <PanelLeft size={16} aria-hidden="true" />
      </button>
      {env && <span className="nx-envbadge" title={t("env.title", { label: env })}>{env}</span>}
      <nav className="nx-crumbs" aria-label={t("crumb.label")}>
        {crumbs.length === 0 ? <span className="nx-crumb-current" aria-current="page">{rootLabel}</span>
          : <button type="button" onClick={() => navigateTo("datacenter", null, "summary")}>{rootLabel}</button>}
        {crumbs.map(([label, go], i) => (
          <span key={i} className="nx-crumb-seg">
            <span className="nx-crumb-sep" aria-hidden="true">/</span>
            {go ? <button type="button" onClick={go}>{label}</button> : <span className="nx-crumb-current" aria-current="page">{label}</span>}
          </span>
        ))}
      </nav>
      <button type="button" className="nx-find" onClick={onOpenPalette} aria-label={t("find.open")}>
        <Search size={15} aria-hidden="true" />
        <span className="nx-find-label">{t("find.placeholder")}</span>
        <span className="nx-kbd" aria-hidden="true">Ctrl K</span>
      </button>
      <button type="button" className="nx-topchip" onClick={() => navigateTo("datacenter", null, "activity")} aria-label={t("top.opsLabel", { n: ops.length })}>
        <LoaderCircle size={15} aria-hidden="true" className={ops.length ? "nx-spin nx-tone-info" : "nx-muted"} />
        <span className="nx-hide-narrow">{ops.length ? t("top.ops", { n: ops.length }) : t("top.opsNone")}</span>
      </button>
      <button type="button" className="nx-topchip" onClick={() => navigateTo("datacenter", null, "summary")} aria-label={t("top.alertsLabel", { n: health.total })}>
        <Bell size={15} aria-hidden="true" />
        {health.total > 0 && <span className="nx-topchip-n" data-tone={health.critical ? "danger" : "warning"}>{health.total}</span>}
      </button>
      {caps.create && (
        <span className="nx-relative">
          <button ref={createBtn} type="button" className="nx-btn nx-btn--primary" aria-haspopup="menu" aria-expanded={createOpen} onClick={() => setCreateOpen((o) => !o)}>
            <Plus size={15} aria-hidden="true" /><span className="nx-hide-narrow">{t("action.create")}</span><ChevronDown size={14} aria-hidden="true" />
          </button>
          <Menu open={createOpen} onClose={() => setCreateOpen(false)} label={t("action.create")} returnFocusRef={createBtn} style={{ top: "calc(100% + 4px)", right: 0 }}>
            <MenuItem onSelect={() => create("vm")}><Monitor size={16} aria-hidden="true" />{t("action.createVm")}</MenuItem>
            <MenuItem onSelect={() => create("container")}><Box size={16} aria-hidden="true" />{t("action.createContainer")}</MenuItem>
            <hr />
            <MenuItem onSelect={() => create("pool")}><Database size={16} aria-hidden="true" />{t("action.createPool")}</MenuItem>
            <MenuItem onSelect={() => create("network")}><Network size={16} aria-hidden="true" />{t("action.createNetwork")}</MenuItem>
            <MenuItem onSelect={() => create("user")}><Users size={16} aria-hidden="true" />{t("action.createUser")}</MenuItem>
          </Menu>
        </span>
      )}

      <CreateVmWizard open={!!wizards.vm} onClose={() => setWizards({})} triggerRef={createBtn} />
      <CreateContainerWizard open={!!wizards.container} onClose={() => setWizards({})} triggerRef={createBtn} />
    </header>
  );
}
