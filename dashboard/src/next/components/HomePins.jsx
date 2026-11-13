import { useShallow } from "zustand/react/shallow";
import { ArrowDown, ArrowUp, ChartLine, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { useExplorerStore } from "../lib/useExplorer";
import ExplorerChart from "./ExplorerChart";

// Home ▸ Dashboard: the charts pinned from the metrics explorer, in the order chosen; one opens back in the explorer.
export default function HomePins() {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const { pins, unpin, move, setChart } = useExplorerStore(useShallow((s) => ({ pins: s.pins, unpin: s.unpin, move: s.move, setChart: s.setChart })));
  if (!pins.length) return null;
  const open = (p) => { setChart({ series: p.series, range: p.range }); navigateTo("datacenter", null, "explorer"); };
  return (
    <section>
      <h2>{t("home.dashboard")}</h2>
      <div className="hc-pins">
        {pins.map((p, i) => (
          <article key={p.id} className="hc-pin" aria-labelledby={`pin-${p.id}`}>
            <div className="hc-pin-h">
              <div className="hc-pin-t"><h3 id={`pin-${p.id}`}>{p.title}</h3><small>{t(`mxp.r.${p.range}`)}</small></div>
              <button type="button" className="hc-ibtn" aria-label={t("mxp.openIn", { name: p.title })} title={t("mxp.openShort")} onClick={() => open(p)}><ChartLine size={15} aria-hidden="true" /></button>
              {pins.length > 1 && <>
                <button type="button" className="hc-ibtn" aria-label={t("mxp.moveUp", { name: p.title })} title={t("mxp.moveUpShort")} disabled={i === 0} onClick={() => move(p.id, -1)}><ArrowUp size={15} aria-hidden="true" /></button>
                <button type="button" className="hc-ibtn" aria-label={t("mxp.moveDown", { name: p.title })} title={t("mxp.moveDownShort")} disabled={i === pins.length - 1} onClick={() => move(p.id, 1)}><ArrowDown size={15} aria-hidden="true" /></button>
              </>}
              <button type="button" className="hc-ibtn" aria-label={t("mxp.unpin", { name: p.title })} title={t("mxp.unpinShort")} onClick={() => unpin(p.id)}><X size={15} aria-hidden="true" /></button>
            </div>
            <ExplorerChart chart={p} height={170} label={p.title} />
          </article>
        ))}
      </div>
    </section>
  );
}
