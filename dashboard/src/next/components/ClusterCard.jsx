import { useEffect, useState } from "react";
import { Copy, KeyRound, Network, Star, Trash2, UserPlus } from "lucide-react";
import { clusterJoinInformation, createCluster, fetchCluster, joinCluster, removeClusterMember, setClusterPrimary } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { InlineError } from "./States";
import { Card, TableWrap } from "./ui";

// The cluster name the join information names (app/core/cluster_setup.py: base64url JSON), or null.
export function clusterOf(information) {
  try {
    const text = information.trim().replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(text + "=".repeat((4 - (text.length % 4)) % 4))).cluster || null;
  } catch {
    return null;
  }
}

// Create a cluster, let another node in, join one, remove a member: all nodes are equal, as on Proxmox
// (app/core/cluster_setup.py). Shown on Administration › Replicated configuration.
export default function ClusterCard() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [create, setCreate] = useState({ name: "", address: "" });
  const [join, setJoin] = useState({ information: "", address: "", confirmation: "" });
  const [info, setInfo] = useState(null);
  const load = () => fetchCluster().then((r) => { setState(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []);

  async function run(action, success) {
    setBusy(true);
    try {
      const result = await action();
      if (success) pushToast({ kind: "success", ...success(result) });
      await load();
      return result;
    } catch (e) {
      pushToast({ kind: "error", title: t("cl.failed"), message: errorMessage(e) });
      return null;
    } finally { setBusy(false); }
  }

  async function onCreate(e) {
    e.preventDefault();
    if (!(await confirmAction({ title: t("cl.createTitle", { name: create.name }), message: t("cl.createMsg"), confirmLabel: t("cl.create"), danger: false }))) return;
    await run(() => createCluster(create), () => ({ title: t("cl.created", { name: create.name }) }));
  }

  async function onJoin(e) {
    e.preventDefault();
    const name = clusterOf(join.information);
    if (!(await confirmAction({ title: t("cl.joinTitle", { name }), message: t("cl.joinMsg"), confirmLabel: t("cl.join") }))) return;
    const done = await run(() => joinCluster(join), () => ({ title: t("cl.joined", { name }), message: t("cl.joinedMsg") }));
    if (done) setJoin({ information: "", address: "", confirmation: "" });
  }

  async function onInformation() {
    const done = await run(() => clusterJoinInformation());
    if (done) setInfo(done);
  }

  // The primary node keeps the quorum alone with one other node: that node never makes it read-only.
  async function onPrimary(member) {
    const unset = member.votes > 1;
    const title = t(unset ? "cl.unsetPrimaryTitle" : "cl.primaryTitle", { name: member.name });
    if (!(await confirmAction({ title, message: t(unset ? "cl.unsetPrimaryMsg" : "cl.primaryMsg", { name: member.name }), confirmLabel: t(unset ? "cl.unsetPrimary" : "cl.makePrimary") }))) return;
    await run(() => setClusterPrimary(unset ? null : member.name), () => ({ title: t(unset ? "cl.unsetPrimaryDone" : "cl.primaryDone", { name: member.name }) }));
  }

  async function onRemove(member) {
    if (!(await confirmAction({ title: t("cl.removeTitle", { name: member.name }), message: t("cl.removeMsg"), confirmLabel: t("cl.remove") }))) return;
    await run(() => removeClusterMember(member.name), () => ({ title: t("cl.removed", { name: member.name }) }));
  }

  if (error && !state) return <Card title={t("cl.title")}><InlineError message={error} onRetry={load} /></Card>;
  if (!state) return null;
  const joinName = clusterOf(join.information);

  if (state.in_cluster) {
    return (
      <Card title={t("cl.inCluster", { name: state.name ?? "…" })}>
        {state.error && <div className="nx-bn" data-tone="danger" role="alert">{state.error}</div>}
        <p className="nx-muted" style={{ margin: "0 0 var(--space-3)" }}>{t(state.quorum ? "cl.quorate" : "cl.noQuorum")}</p>
        <TableWrap label={t("cl.members")}>
          <table className="nx-table">
            <thead><tr><th scope="col">{t("cl.member")}</th><th scope="col">{t("cl.nodeid")}</th><th scope="col">{t("cl.address")}</th><th scope="col">{t("cl.votes")}</th><th scope="col"><span className="nx-sr">{t("cl.actions")}</span></th></tr></thead>
            <tbody>
              {state.members.map((m) => (
                <tr key={m.name}>
                  <th scope="row">{m.name}{m.name === state.node && <span className="nx-f-h"> — {t("cl.thisNode")}</span>}</th>
                  <td>{m.nodeid}</td>
                  <td className="nx-mono">{m.address}</td>
                  <td>{m.votes > 1 ? <span className="nx-chip" data-tone="info" title={t("cl.primaryHelp")}><Star size={12} aria-hidden="true" /> {t("cl.primary")} · {m.votes}</span> : (m.votes ?? 1)}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn" disabled={busy} onClick={() => onPrimary(m)}>{t(m.votes > 1 ? "cl.unsetPrimary" : "cl.makePrimary")}</button>
                    {m.name !== state.node && (
                    <button type="button" className="nx-btn" disabled={busy} onClick={() => onRemove(m)} aria-label={t("cl.removeLabel", { name: m.name })}><Trash2 size={15} aria-hidden="true" />{t("cl.remove")}</button>
                    )}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
        <div style={{ marginTop: "var(--space-3)" }}>
          <button type="button" className="nx-btn nx-btn--primary" disabled={busy} onClick={onInformation}><KeyRound size={15} aria-hidden="true" />{t("cl.information")}</button>
        </div>
        {info && (
          <div className="nx-form" style={{ marginTop: "var(--space-3)" }}>
            <label>{t("cl.informationLabel")}
              <textarea className="nx-input nx-mono" readOnly rows={4} value={info.information} onFocus={(e) => e.target.select()} />
            </label>
            <p className="nx-f-h">{t("cl.informationHelp", { when: formatDateTime(info.expire, lang) })}</p>
            <div><button type="button" className="nx-btn" onClick={() => navigator.clipboard?.writeText(info.information)}><Copy size={15} aria-hidden="true" />{t("cl.copy")}</button></div>
          </div>
        )}
      </Card>
    );
  }

  return (
    <Card title={t("cl.title")}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)" }}>{t("cl.intro")}</p>
      {!state.corosync_installed && <div className="nx-bn" data-tone="warning" role="note">{t("cl.noCorosync")}</div>}
      <div className="nx-formgrid">
        <form className="nx-form" onSubmit={onCreate} aria-labelledby="cl-create">
          <h3 id="cl-create" className="nx-f-label">{t("cl.createHeading")}</h3>
          <label>{t("cl.name")}<input className="nx-input" value={create.name} maxLength={15} onChange={(e) => setCreate({ ...create, name: e.target.value })} placeholder="prod" autoComplete="off" /></label>
          <label>{t("cl.addressOfThisNode")}<input className="nx-input nx-mono" value={create.address} onChange={(e) => setCreate({ ...create, address: e.target.value })} placeholder="192.0.2.10" autoComplete="off" /></label>
          <p className="nx-f-h">{t("cl.addressHelp")}</p>
          <div><button type="submit" className="nx-btn nx-btn--primary" disabled={busy || !state.corosync_installed || !create.name || !create.address}><Network size={15} aria-hidden="true" />{t("cl.create")}</button></div>
        </form>
        <form className="nx-form" onSubmit={onJoin} aria-labelledby="cl-join">
          <h3 id="cl-join" className="nx-f-label">{t("cl.joinHeading")}</h3>
          <label>{t("cl.informationLabel")}<textarea className="nx-input nx-mono" rows={4} value={join.information} onChange={(e) => setJoin({ ...join, information: e.target.value })} spellCheck={false} /></label>
          <label>{t("cl.addressOfThisNode")}<input className="nx-input nx-mono" value={join.address} onChange={(e) => setJoin({ ...join, address: e.target.value })} placeholder="192.0.2.11" autoComplete="off" /></label>
          <div className="nx-notice nx-notice--warning" role="note">{t("cl.joinWarning")}</div>
          <label>{joinName ? t("cl.typeName", { name: joinName }) : t("cl.typeNameWait")}<input className="nx-input nx-mono" value={join.confirmation} onChange={(e) => setJoin({ ...join, confirmation: e.target.value })} autoComplete="off" spellCheck={false} /></label>
          <div><button type="submit" className="nx-btn nx-btn--danger" disabled={busy || !state.corosync_installed || !joinName || !join.address || join.confirmation.trim() !== joinName}><UserPlus size={15} aria-hidden="true" />{t("cl.join")}</button></div>
        </form>
      </div>
    </Card>
  );
}
