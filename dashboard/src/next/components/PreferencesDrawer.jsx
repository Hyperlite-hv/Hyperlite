import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { poolKey, TERM_FONTS, TERM_SIZES, usePrefs } from "../lib/prefs";
import { Field, SideDrawer } from "./ui";

// "My preferences": how this browser shows terminals and which storage pools the Home page follows. Saved as they
// change (no Save button): nothing here touches the server.
export default function PreferencesDrawer({ open, onClose }) {
  const t = useT();
  const prefs = usePrefs();
  const pools = useInfraStore((s) => s.storagePools);
  const nodes = useInfraStore((s) => s.nodes);
  const chosen = prefs.overviewPools;
  const isShown = (p) => !chosen || chosen.includes(poolKey(p));
  function toggle(p) {
    const all = pools.map(poolKey);
    const current = chosen || all;
    const next = current.includes(poolKey(p)) ? current.filter((k) => k !== poolKey(p)) : [...current, poolKey(p)];
    // Every pool chosen again means "no choice": pools created later then show up on their own.
    prefs.update({ overviewPools: all.every((k) => next.includes(k)) ? null : next });
  }
  return (
    <SideDrawer open={open} title={t("prefs.title")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={prefs.reset}>{t("prefs.reset")}</button>
      <button type="button" className="nx-btn nx-btn--primary" onClick={onClose}>{t("prefs.done")}</button>
    </>}>
      <p className="nx-muted" style={{ margin: 0 }}>{t("prefs.help")}</p>
      <fieldset className="nx-fs">
        <legend>{t("prefs.terminal")}</legend>
        <div className="nx-fg">
          <Field label={t("prefs.font")}>{(p) => <select {...p} className="nx-inp" value={prefs.termFont} onChange={(e) => prefs.update({ termFont: e.target.value })}>
            {Object.keys(TERM_FONTS).map((k) => <option key={k} value={k}>{t(`prefs.font.${k}`)}</option>)}
          </select>}</Field>
          <Field label={t("prefs.size")}>{(p) => <select {...p} className="nx-inp" value={prefs.termFontSize} onChange={(e) => prefs.update({ termFontSize: Number(e.target.value) })}>
            {TERM_SIZES.map((n) => <option key={n} value={n}>{n} px</option>)}
          </select>}</Field>
        </div>
        <div className="nx-term-sample" style={{ fontFamily: TERM_FONTS[prefs.termFont], fontSize: prefs.termFontSize }} aria-label={t("prefs.sample")}>root@hv1:~# virsh list --all</div>
        <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-13)" }}>{t("prefs.termNote")}</p>
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("prefs.pools")}</legend>
        {pools.length === 0 ? <p className="nx-muted" style={{ margin: 0 }}>{t("ov.noPools")}</p> : (
          <div className="nx-checks">{pools.map((p) => (
            <label key={poolKey(p)} className="nx-check"><input type="checkbox" checked={isShown(p)} onChange={() => toggle(p)} /> {p.nom} <span className="nx-muted">· {nodes.find((n) => n.id === p.node)?.nom || p.node}</span></label>
          ))}</div>
        )}
      </fieldset>
    </SideDrawer>
  );
}
