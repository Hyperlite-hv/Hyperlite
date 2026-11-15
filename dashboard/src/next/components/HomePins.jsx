import { useEffect } from "react";
import { LayoutDashboard } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { moveOldPins, useDashboards } from "../lib/dashboards";
import { useExplorerStore } from "../lib/useExplorer";
import { DashboardGrid } from "../pages/DashboardsPage";

// Home ▸ Dashboard: the dashboard chosen to show on Home ("Show on Home" on the Dashboards page). The charts pinned
// here before dashboards existed move once to a first private dashboard.
export default function HomePins() {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const { list, load, home } = useDashboards();
  const pins = useExplorerStore((s) => s.pins);
  useEffect(() => { load().then(() => moveOldPins(pins, t("dsh.mine")).catch(() => {})); }, [load]); // eslint-disable-line react-hooks/exhaustive-deps
  const dash = (list || []).find((d) => d.id === home);
  if (!dash) return null;
  return (
    <section>
      <div className="hc-home-dash-h">
        <h2>{dash.name}</h2>
        <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "dashboards")}><LayoutDashboard size={14} aria-hidden="true" /> {t("dsh.openAll")}</button>
      </div>
      <DashboardGrid dash={dash} editing={false} onChange={() => {}} />
    </section>
  );
}
