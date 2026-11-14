import { useShallow } from "zustand/react/shallow";
import { ArrowDown, ArrowUp, Check, X } from "lucide-react";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { poolKey, TERM_FONTS, TERM_SIZES, usePrefs } from "../lib/prefs";
import { ACCENTS, DENSITIES, SCALES, useAppearance } from "../lib/appearance";
import { allowedServices, serviceLabel, useConsoleStore } from "../lib/services";
import { capabilities } from "../lib/capabilities";
import { useApiDocsAccess } from "../layout/SideMenu";
import { useThemeStore } from "../tokens/theme";
import { Field, SideDrawer } from "./ui";
import ServiceTile from "./ServiceTile";

// One choice among a few, as a row of toggle buttons.
function Choice({ label, value, options, onChange }) {
  return (
    <div className="hc-choice" role="group" aria-label={label}>
      <span>{label}</span>
      <div className="nx-seg">{options.map(([v, text]) => <button key={String(v)} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>{text}</button>)}</div>
    </div>
  );
}

// "My preferences": how this browser shows terminals and which storage pools the Home page follows. Saved as they
// change (no Save button): nothing here touches the server.
export default function PreferencesDrawer({ open, onClose }) {
  const t = useT();
  const prefs = usePrefs();
  const pools = useInfraStore((s) => s.storagePools);
  const nodes = useInfraStore((s) => s.nodes);
  const look = useAppearance();
  const { mode, setMode } = useThemeStore(useShallow((s) => ({ mode: s.mode, setMode: s.setMode })));
  const { favorites, moveFavorite, toggleFavorite, resetFavorites } = useConsoleStore(useShallow((s) => ({ favorites: s.favorites, moveFavorite: s.moveFavorite, toggleFavorite: s.toggleFavorite, resetFavorites: s.resetFavorites })));
  const caps = capabilities(useAuthStore((s) => s.role));
  const apiDocs = useApiDocsAccess(caps.admin);
  const services = allowedServices({ admin: caps.admin, apiDocs });
  const resetAll = () => { prefs.reset(); look.reset(); setMode("system"); resetFavorites(); };
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
      <button type="button" className="nx-btn nx-btn--ghost" onClick={resetAll}>{t("prefs.reset")}</button>
      <button type="button" className="nx-btn nx-btn--primary" onClick={onClose}>{t("prefs.done")}</button>
    </>}>
      <p className="nx-muted" style={{ margin: 0 }}>{t("prefs.help")}</p>
      <fieldset className="nx-fs">
        <legend>{t("prefs.look")}</legend>
        <Choice label={t("top.theme")} value={mode} onChange={setMode} options={["system", "light", "dark"].map((m) => [m, t(`top.theme.${m}`)])} />
        <div className="hc-choice" role="group" aria-label={t("prefs.accent")}>
          <span>{t("prefs.accent")}</span>
          <div className="hc-swatches">{ACCENTS.map((a) => (
            <button key={a} type="button" aria-pressed={look.accent === a} className="hc-swatch" data-swatch={a} title={t(`prefs.accent.${a}`)} aria-label={t(`prefs.accent.${a}`)} onClick={() => look.update({ accent: a })}>{look.accent === a && <Check size={14} aria-hidden="true" />}</button>
          ))}</div>
        </div>
        <Choice label={t("prefs.density")} value={look.density} onChange={(density) => look.update({ density })} options={DENSITIES.map((d) => [d, t(`prefs.density.${d}`)])} />
        <Choice label={t("prefs.scale")} value={look.scale} onChange={(scale) => look.update({ scale })} options={SCALES.map((x) => [x, `${Math.round(x * 100)} %`])} />
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("prefs.start")}</legend>
        <Field label={t("prefs.startPage")} hint={t("prefs.startHelp")}>{(p) => <select {...p} className="nx-inp" value={look.startPage} onChange={(e) => look.update({ startPage: e.target.value })}>
          <option value="summary">{t("nav.overview")}</option>
          {services.map((s) => <option key={s.id} value={s.id}>{t(serviceLabel(s.id))}</option>)}
        </select>}</Field>
      </fieldset>
      <fieldset className="nx-fs">
        <legend>{t("prefs.sideMenu")}</legend>
        <p className="nx-hint" style={{ margin: 0 }}>{t("prefs.menuHelp")}</p>
        <ol className="hc-favs">{favorites.map((id, i) => (
          <li key={id}><ServiceTile id={id} size={18} /><span>{t(serviceLabel(id))}</span>
            <button type="button" className="hc-ibtn" disabled={i === 0} aria-label={t("prefs.up", { name: t(serviceLabel(id)) })} onClick={() => moveFavorite(id, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
            <button type="button" className="hc-ibtn" disabled={i === favorites.length - 1} aria-label={t("prefs.down", { name: t(serviceLabel(id)) })} onClick={() => moveFavorite(id, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
            <button type="button" className="hc-ibtn" aria-label={t("prefs.unpin", { name: t(serviceLabel(id)) })} onClick={() => toggleFavorite(id)}><X size={14} aria-hidden="true" /></button>
          </li>
        ))}</ol>
        <Field label={t("prefs.pin")}>{(p) => <select {...p} className="nx-inp" value="" onChange={(e) => e.target.value && toggleFavorite(e.target.value)}>
          <option value="">{t("prefs.pinPick")}</option>
          {services.filter((s) => !favorites.includes(s.id)).map((s) => <option key={s.id} value={s.id}>{t(serviceLabel(s.id))}</option>)}
        </select>}</Field>
      </fieldset>
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
            <label key={poolKey(p)} className="nx-check"><input type="checkbox" checked={isShown(p)} onChange={() => toggle(p)} /> {p.name} <span className="nx-muted">· {nodes.find((n) => n.id === p.node)?.name || p.node}</span></label>
          ))}</div>
        )}
      </fieldset>
    </SideDrawer>
  );
}
