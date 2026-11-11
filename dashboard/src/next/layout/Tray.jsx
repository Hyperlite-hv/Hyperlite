import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { taskLabel } from "../lib/enums";
import { useRunningOps } from "./TopBar";

// A strip under the workspace while something runs: each operation with its progress when the task reports one.
// It disappears when nothing runs, so a calm cluster shows nothing here.
export default function Tray() {
  const t = useT();
  const ops = useRunningOps();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  if (ops.length === 0) return null;
  return (
    <section className="nx-tray" aria-label={t("tray.label")}>
      <strong>{t("tray.count", { n: ops.length })}</strong>
      <ul>
        {ops.slice(0, 4).map((o) => {
          const p = typeof o.progress === "number" && o.progress > 0 ? Math.min(100, Math.round(o.progress)) : null;
          return (
            <li key={o.id}>
              <span className="nx-tray-lbl">{taskLabel(o.type)}{o.target ? <> · <b>{o.target}</b></> : null}{o.node ? <span className="nx-muted"> · {o.node}</span> : null}</span>
              <span className={`nx-tray-bar${p == null ? " is-indet" : ""}`} role="progressbar" aria-label={taskLabel(o.type)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={p ?? undefined}>
                <span style={p == null ? undefined : { width: `${p}%` }} />
              </span>
              {p != null && <span className="nx-tray-pct">{p} %</span>}
            </li>
          );
        })}
        {ops.length > 4 && <li className="nx-muted">{t("tray.more", { n: ops.length - 4 })}</li>}
      </ul>
      <button type="button" className="nx-lnk" onClick={() => navigateTo("datacenter", null, "activity")}>{t("tray.journal")}</button>
    </section>
  );
}
