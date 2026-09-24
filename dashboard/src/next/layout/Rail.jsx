import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { sectionOfTab } from "../legacy/tabs";

const ICONS = {
  overview: <path d="M2 9.5 10 3l8 6.5V17H2zM7.5 17v-5h5v5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />,
  infrastructure: <g fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2.5" y="3" width="15" height="5" rx="1" /><rect x="2.5" y="12" width="15" height="5" rx="1" /><path d="M5.5 5.5h.01M5.5 14.5h.01" strokeLinecap="round" /></g>,
  vms: <g fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2.5" y="3.5" width="15" height="10" rx="1.2" /><path d="M7 17h6M10 13.5V17" strokeLinecap="round" /></g>,
  activity: <path d="M2 10h3l2-6 4 12 2-6h5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />,
  security: <path d="M10 2.5 16.5 5v5c0 3.6-2.6 6-6.5 7.5C6.1 16 3.5 13.6 3.5 10V5z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />,
  settings: <g fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M3 6h9M15 6h2M3 14h2M8 14h9" /><circle cx="13.5" cy="6" r="1.8" /><circle cx="6.5" cy="14" r="1.8" /></g>,
};

// Section rail. Sections are entry points onto the same views as before: Overview/Infrastructure/
// Activity/Security/Settings open Datacenter tabs (all 16 stay reachable); Virtual Machines opens
// the VM table of the local node until the dedicated list is rebuilt.
export default function Rail() {
  const t = useT();
  const selection = useInfraStore((s) => s.selection);
  const activeTab = useInfraStore((s) => s.activeTab);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const nodes = useInfraStore((s) => s.nodes);

  const current = selection.type === "vm" ? "vms"
    : selection.type === "node" ? "infrastructure"
    : selection.type === "datacenter" ? sectionOfTab(activeTab) : "overview";

  const items = [
    ["overview", () => navigateTo("datacenter", null, "summary")],
    ["infrastructure", () => navigateTo("datacenter", null, "nodes")],
    ["vms", () => navigateTo("node", nodes[0]?.id || "local", "summary")],
    ["activity", () => navigateTo("datacenter", null, "activity")],
    ["security", () => navigateTo("datacenter", null, "permissions")],
    ["settings", () => navigateTo("datacenter", null, "automation")],
  ];

  return (
    <nav className="nx-rail" aria-label={t("nav.main")}>
      {items.map(([id, go]) => (
        <button key={id} type="button" aria-current={current === id ? "page" : undefined} onClick={go}>
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">{ICONS[id]}</svg>
          <span>{t(`nav.${id}`)}</span>
        </button>
      ))}
    </nav>
  );
}
