import { useId, useState } from "react";
import { LifeBuoy, Search } from "lucide-react";
import { scanSiteBackups, startSiteRecovery } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatSizeMb } from "../lib/format";
import { InlineError } from "./States";
import { Card, Empty, Field, TableWrap } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

// Recovery of a lost site: the backups the other site wrote to a storage this node reads (an NFS share), restored
// here as new VMs. Deciding that the other site is lost stays with the administrator: from here, a dead site and a
// cut link look the same.
export default function SiteRecovery() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const pools = useInfraStore((s) => s.storagePools);
  const networks = useInfraStore((s) => s.networks);
  const listId = useId();
  const [dir, setDir] = useState("");
  const [found, setFound] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState({}); // vm -> {on, nom}
  const [network, setNetwork] = useState("");

  const localPools = (pools || []).filter((p) => p.node === "local" && p.chemin);
  const localNetworks = [...new Set((networks || []).filter((n) => !n.node || n.node === "local").map((n) => n.nom))].sort();

  async function scan() {
    setBusy(true); setError(null);
    try {
      const vms = await scanSiteBackups(dir.trim());
      setFound(vms);
      setChosen(Object.fromEntries(vms.map((v) => [v.vm, { on: !v.existe_ici, nom: v.existe_ici ? `${v.vm}-recup`.slice(0, 63) : v.vm }])));
    } catch (e) { setFound(null); setError(errorMessage(e)); }
    finally { setBusy(false); }
  }

  const selected = (found || []).filter((v) => chosen[v.vm]?.on);
  const badName = (v) => !NAME_RE.test(chosen[v.vm]?.nom || "");
  const names = selected.map((v) => chosen[v.vm].nom);
  const duplicate = names.length !== new Set(names).size;
  const invalid = selected.length === 0 || selected.some(badName) || duplicate;

  async function restore() {
    const message = t("srec.confirmMsg", { list: names.join(", ") });
    if (!(await confirmAction({ title: t("srec.confirmTitle", { n: selected.length }), message, confirmLabel: t("srec.restore") }))) return;
    setBusy(true);
    try {
      await startSiteRecovery(selected.map((v) => ({ chemin: v.sauvegardes[0].chemin, nom: chosen[v.vm].nom })), network);
      pushToast({ kind: "success", title: t("srec.started"), message: t("srec.startedMsg", { n: selected.length }) });
      navigateTo("datacenter", null, "activity");
    } catch (e) { pushToast({ kind: "error", title: t("srec.failed"), message: errorMessage(e) }); }
    finally { setBusy(false); }
  }

  return (
    <Card title={t("srec.title")}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("srec.help")}</p>
      <div className="nx-fg" style={{ alignItems: "end" }}>
        <Field label={t("srec.dir")} hint={t("srec.dirHint")}>{(p) => (
          <input {...p} className="nx-inp nx-mono" list={listId} value={dir} placeholder="/var/lib/libvirt/hyperlite-pools/site-a" onChange={(e) => setDir(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && dir.trim()) scan(); }} />
        )}</Field>
        <datalist id={listId}>{localPools.map((p) => <option key={p.nom} value={p.chemin}>{p.nom}</option>)}</datalist>
        <div><button type="button" className="nx-btn" disabled={!dir.trim() || busy} onClick={scan}><Search size={15} aria-hidden="true" />{t("srec.scan")}</button></div>
      </div>
      {error && <InlineError message={error} onRetry={scan} />}
      {found && found.length === 0 && <Empty icon={LifeBuoy} title={t("srec.none")} text={t("srec.noneHelp")} />}
      {found && found.length > 0 && (
        <>
          <TableWrap>
            <table className="nx-table">
              <caption className="nx-sr">{t("srec.title")}</caption>
              <thead><tr>
                <th scope="col"><span className="nx-sr">{t("srec.pick")}</span></th>
                <th scope="col">VM</th><th scope="col">{t("srec.latest")}</th><th scope="col">{t("srec.source")}</th>
                <th scope="col" className="nx-num">{t("bk.size")}</th><th scope="col">{t("srec.newName")}</th>
              </tr></thead>
              <tbody>
                {found.map((v) => {
                  const b = v.sauvegardes[0];
                  const c = chosen[v.vm] || { on: false, nom: v.vm };
                  return (
                    <tr key={v.vm}>
                      <td><input type="checkbox" aria-label={t("srec.pickX", { vm: v.vm })} checked={c.on} onChange={(e) => setChosen((s) => ({ ...s, [v.vm]: { ...c, on: e.target.checked } }))} /></td>
                      <th scope="row">{v.vm}{v.existe_ici && <div className="nx-f-h">{t("srec.existsHere")}</div>}</th>
                      <td className="nx-mono">{b.cree_le ? formatDateTime(b.cree_le, lang) : "—"}{v.sauvegardes.length > 1 && <div className="nx-f-h">{t("srec.older", { n: v.sauvegardes.length - 1 })}</div>}</td>
                      <td>{b.source || "—"}</td>
                      <td className="nx-num nx-mono">{formatSizeMb(b.taille_octets / 1048576, lang)}</td>
                      <td>
                        <input className="nx-inp nx-mono" aria-label={t("srec.newNameX", { vm: v.vm })} value={c.nom} disabled={!c.on} aria-invalid={c.on && badName(v) ? true : undefined}
                          onChange={(e) => setChosen((s) => ({ ...s, [v.vm]: { ...c, nom: e.target.value } }))} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
          <div className="nx-fg" style={{ alignItems: "end", marginTop: "var(--space-3)" }}>
            <Field label={t("srec.network")} hint={t("srec.networkHint")}>{(p) => (
              <select {...p} className="nx-inp" value={network} onChange={(e) => setNetwork(e.target.value)}>
                <option value="">{t("srec.networkAsRecorded")}</option>
                {localNetworks.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            )}</Field>
            <div><button type="button" className="nx-btn nx-btn--primary" disabled={invalid || busy} onClick={restore}><LifeBuoy size={15} aria-hidden="true" />{t("srec.restoreN", { n: selected.length })}</button></div>
          </div>
          {duplicate && <p className="nx-tone-danger" role="alert">{t("srec.duplicate")}</p>}
          {selected.some(badName) && <p className="nx-tone-danger" role="alert">{t("srec.badName")}</p>}
        </>
      )}
    </Card>
  );
}
