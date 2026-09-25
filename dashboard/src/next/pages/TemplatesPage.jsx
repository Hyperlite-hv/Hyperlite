import { useCallback, useEffect, useState } from "react";
import { fetchTemplates, deployTemplate, deleteTemplate } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { EmptyState, ErrorState } from "../components/States";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

// Templates: VMs converted to read-only sources. Converting a stopped VM is done from its own page;
// this page deploys a copy under a validated name or deletes the template (and its disk).
export default function TemplatesPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const loadAll = useInfraStore((s) => s.loadAll);
  const vms = useInfraStore((s) => s.vms);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const reload = useCallback(async () => {
    try { const r = await fetchTemplates(); setItems(Array.isArray(r) ? r : []); setError(null); }
    catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { reload(); }, [reload]);

  async function deploy(tpl) {
    const taken = new Set(vms.map((v) => v.nom));
    const name = await promptText({
      title: t("tp.deployTitle", { name: tpl.nom }), label: t("tp.newName"), defaultValue: `${tpl.nom}-01`, confirmLabel: t("tp.deploy"),
      validate: (v) => (!NAME_RE.test(v) ? t("ct.nameRule") : taken.has(v) ? t("tp.nameTaken") : ""),
    });
    if (!name || !name.trim()) return;
    setBusy(tpl.nom);
    try { await deployTemplate(tpl.nom, name.trim()); pushToast({ kind: "success", title: t("tp.deployed"), message: name.trim() }); await loadAll(); }
    catch (e) { pushToast({ kind: "error", title: t("tp.deployFailed"), message: errorMessage(e) }); }
    finally { setBusy(null); }
  }
  async function remove(tpl) {
    if (!(await confirmAction({ title: t("tp.deleteTitle", { name: tpl.nom }), message: t("tp.deleteMsg"), confirmLabel: t("menu.delete").replace("…", ""), danger: true }))) return;
    setBusy(tpl.nom);
    try { await deleteTemplate(tpl.nom); pushToast({ kind: "success", title: t("tp.deleted"), message: tpl.nom }); await reload(); }
    catch (e) { pushToast({ kind: "error", title: t("tp.deleteFailed"), message: errorMessage(e) }); }
    finally { setBusy(null); }
  }

  if (error && items == null) return <ErrorState message={error} onRetry={reload} />;
  const list = items || [];
  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="tp-title">
        <div className="nx-cardhead"><h2 id="tp-title">{t("tab.templates")} <span className="nx-count">{items ? list.length : "…"}</span></h2></div>
        <p className="nx-muted" style={{ marginTop: 0 }}>{t("tp.intro")}</p>
        {items == null ? <p className="nx-muted" role="status">{t("loading")}</p> : list.length === 0 ? <EmptyState title={t("tp.none")} help={t("tp.noneHelp")} /> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ct.name")}</th><th scope="col">{t("tp.source")}</th><th scope="col" className="nx-num">vCPU</th><th scope="col" className="nx-num">{t("ct.memory")}</th><th scope="col">{t("tp.created")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {list.map((tpl) => (
                  <tr key={tpl.nom}>
                    <th scope="row" className="nx-mono">{tpl.nom}</th>
                    <td className="nx-mono">{tpl.vm_source}</td>
                    <td className="nx-num nx-mono">{tpl.vcpu}</td>
                    <td className="nx-num nx-mono">{formatSizeMb(tpl.memoire_mo, lang)}</td>
                    <td>{tpl.cree_par} · <span className="nx-mono">{tpl.cree_le}</span></td>
                    <td className="nx-num nx-rowactions">
                      {caps.admin && <button type="button" className="nx-btn nx-btn--primary" disabled={busy === tpl.nom} aria-label={`Deploy template ${tpl.nom}`} onClick={() => deploy(tpl)}>{t("tp.deploy")}</button>}
                      {caps.admin && <button type="button" className="nx-btn nx-btn--danger" disabled={busy === tpl.nom} aria-label={`Delete template ${tpl.nom}`} onClick={() => remove(tpl)}>{t("menu.delete").replace("…", "")}</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
