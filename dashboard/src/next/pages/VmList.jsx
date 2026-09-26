import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import VmCollection from "../components/VmCollection";

// "Virtual machines": every VM of every node in one working table (problems first by default).
export default function VmList() {
  const t = useT();
  const vms = useInfraStore(useShallow((s) => s.vms));
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  return (
    <div className="nx-ns">
      <VmCollection vms={vms} showNode headingId="vmlist-h" title={t("nav.vms")} action={caps.create ? <button type="button" className="nx-btn nx-btn--primary" onClick={() => window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" }))}>{t("vmlist.create")}</button> : null} />
    </div>
  );
}
