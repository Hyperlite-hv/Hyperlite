import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, TriangleAlert } from "lucide-react";
import {
  fetchHostNetwork, previewHostNetwork, applyHostNetwork, confirmHostNetwork, rollbackHostNetwork,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, SideDrawer, TableWrap } from "../components/ui";

// The node's own bridges, bonds and VLAN interfaces (app/core/host_network.py). Changes are drafted here, shown as a
// diff, applied, then confirmed from this page within the delay: without the confirmation the node puts its previous
// configuration back by itself, so a change that cuts this browser off undoes itself.

const BOND_MODES = ["active-backup", "802.3ad", "balance-rr", "balance-xor", "broadcast", "balance-tlb", "balance-alb"];
const HASHED = ["802.3ad", "balance-xor"];
const DELAYS = [60, 120, 300];
const NAME = /^[a-zA-Z][a-zA-Z0-9_-]{0,14}$/;
const CIDR = /^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)\/([0-9]|[12]\d|3[0-2])$/;
const IP = /^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)$/;

const blank = (type) => ({ type, name: "", ports: [], slaves: [], mode: "active-backup", hash_policy: "layer2", raw_device: "", vid: "", address: "manual", gateway: "", mtu: "", autostart: true });
const members = (i) => [...(i.ports || []), ...(i.slaves || []), ...(i.type === "vlan" ? [i.raw_device] : [])];
const nameOf = (i) => (i.type === "vlan" ? `${i.raw_device}.${i.vid}` : i.name);

function toWire(i) {
  const out = { type: i.type, address: i.address || "manual", gateway: i.gateway || null, mtu: i.mtu ? Number(i.mtu) : null, autostart: i.autostart !== false };
  if (i.type === "vlan") return { ...out, raw_device: i.raw_device, vid: Number(i.vid) };
  if (i.type === "bridge") return { ...out, name: i.name, ports: i.ports || [] };
  return { ...out, name: i.name, slaves: i.slaves || [], mode: i.mode, hash_policy: i.hash_policy };
}

function useCountdown(deadline) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!deadline) return undefined;
    const id = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(id);
  }, [deadline]);
  return deadline ? Math.max(0, Math.ceil(deadline - now)) : null;
}

export function HostNetworkEditor({ node }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const target = node?.id === "local" ? null : node?.name;
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState(null); // the managed interfaces as edited here, null: none edited
  const [editing, setEditing] = useState(null); // {index (null: new), value}
  const [preview, setPreview] = useState(null);
  const [delay, setDelay] = useState(60);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setState(await fetchHostNetwork(target)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [target]);
  useEffect(() => { load(); }, [load]);

  const pending = state?.pending;
  const left = useCountdown(pending?.deadline);
  // The deadline passed: the node put the previous configuration back, read it again.
  useEffect(() => { if (pending && left === 0) { const id = setTimeout(load, 3000); return () => clearTimeout(id); } return undefined; }, [pending, left, load]);

  const managed = useMemo(() => draft ?? state?.managed ?? [], [draft, state]);
  const changedHere = draft !== null;

  const candidates = useMemo(() => {
    // What an interface can sit on: the node's NICs, bonds, VLANs and bridges nobody configured, and the interfaces of
    // this draft, each with its kind. The administrator's own interfaces stay theirs.
    const kinds = new Map(managed.map((i) => [nameOf(i), i.type]));
    for (const i of state?.interfaces || []) {
      if (["physical", "bond", "vlan", "bridge"].includes(i.type) && !i.configured_by && !kinds.has(i.name)) kinds.set(i.name, i.type);
    }
    return [...kinds.entries()].map(([name, type]) => ({ name, type })).sort((a, b) => a.name.localeCompare(b.name));
  }, [state, managed]);

  if (error && !state) return <ErrorState message={error} onRetry={load} />;
  if (!state) return <Loading />;

  const taken = (except) => new Set(managed.filter((_, k) => k !== except).flatMap(members));
  const tools = state.tools || {};
  const system = (state.interfaces || []).filter((i) => i.configured_by === "system");

  function save(value, index) {
    const list = [...managed];
    if (index === null) list.push(value); else list[index] = value;
    setDraft(list);
    setEditing(null);
  }
  async function remove(index) {
    const i = managed[index];
    if (!(await confirmAction({ title: t("hn.deleteTitle", { name: nameOf(i) }), message: t("hn.deleteMsg"), confirmLabel: t("hn.delete"), danger: true }))) return;
    setDraft(managed.filter((_, k) => k !== index));
  }
  async function review() {
    setBusy(true);
    try { setPreview(await previewHostNetwork(managed.map(toWire), target)); } catch (e) { pushToast({ kind: "error", title: t("hn.checkFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function apply() {
    setBusy(true);
    try {
      const result = await applyHostNetwork(managed.map(toWire), delay, target);
      setPreview(null);
      setDraft(null);
      if (!result.id) pushToast({ kind: "info", title: t("hn.nothing") });
      await load();
    } catch (e) {
      pushToast({ kind: "error", title: t("hn.applyFailed"), message: errorMessage(e) });
      await load();
    } finally { setBusy(false); }
  }
  async function confirm() {
    setBusy(true);
    try { await confirmHostNetwork(pending.id, target); pushToast({ kind: "success", title: t("hn.confirmed") }); await load(); } catch (e) { pushToast({ kind: "error", title: t("hn.confirmFailed"), message: errorMessage(e) }); await load(); } finally { setBusy(false); }
  }
  async function revert() {
    setBusy(true);
    try { await rollbackHostNetwork(pending.id, target); pushToast({ kind: "success", title: t("hn.rolledBack") }); await load(); } catch (e) { pushToast({ kind: "error", title: t("hn.rollbackFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }

  return (
    <>
      {pending && (
        <div className="nx-bn" data-tone="warning" role="status" aria-live="polite">
          <TriangleAlert size={16} aria-hidden="true" />
          <span className="nx-bn-t"><b>{t("hn.pendingTitle", { n: left ?? 0 })}</b> {t("hn.pendingText", { names: pending.interfaces.join(", ") })}</span>
          <button type="button" className="nx-btn nx-btn--sm" onClick={revert} disabled={busy}>{t("hn.rollback")}</button>
          <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" onClick={confirm} disabled={busy || left === 0}>{t("hn.confirm")}</button>
        </div>
      )}
      <Card title={t("hn.title")}>
        {!pending && (
          <div className="nx-bar" role="group" aria-label={t("hn.create")}>
            <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ index: null, value: blank("bridge") })} disabled={!tools.bridge}><Plus size={14} aria-hidden="true" />{t("hn.newBridge")}</button>
            <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ index: null, value: blank("bond") })} disabled={!tools.bond} title={tools.bond ? undefined : t("hn.noIfenslave")}><Plus size={14} aria-hidden="true" />{t("hn.newBond")}</button>
            <button type="button" className="nx-btn nx-btn--sm" onClick={() => setEditing({ index: null, value: blank("vlan") })}><Plus size={14} aria-hidden="true" />{t("hn.newVlan")}</button>
          </div>
        )}
        {!tools.bond && <p className="nx-muted" style={{ marginTop: 0 }}>{t("hn.noIfenslave")}</p>}
        {managed.length === 0 ? <Empty title={t("hn.empty")} text={t("hn.emptyHelp")} /> : (
          <TableWrap>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("nn.iface")}</th><th scope="col">{t("stor.type")}</th><th scope="col">{t("hn.members")}</th><th scope="col">{t("hn.address")}</th><th scope="col" className="nx-num">MTU</th><th scope="col"><span className="nx-sr">{t("hn.actions")}</span></th></tr></thead>
              <tbody>
                {managed.map((i, k) => (
                  <tr key={nameOf(i)}>
                    <th scope="row" className="nx-mono" style={{ fontWeight: 500 }}>{nameOf(i)}</th>
                    <td><Chip>{t(`nn.type.${i.type}`)}</Chip>{i.type === "bond" && <span className="nx-muted"> {i.mode}</span>}</td>
                    <td className="nx-mono nx-wrapcell">{members(i).filter(Boolean).join(", ") || <span className="nx-muted">—</span>}</td>
                    <td>{i.address === "manual" ? <span className="nx-muted">{t("hn.addrNone")}</span> : i.address === "dhcp" ? "DHCP" : <span className="nx-mono">{i.address}</span>}{i.gateway ? <span className="nx-muted nx-mono"> → {i.gateway}</span> : null}</td>
                    <td className="nx-num nx-mono">{i.mtu || "—"}</td>
                    <td className="nx-num">
                      {!pending && <>
                        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setEditing({ index: k, value: { ...blank(i.type), ...i, vid: i.vid ?? "", mtu: i.mtu ?? "", gateway: i.gateway ?? "" } })}>{t("hn.edit")}</button>
                        <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => remove(k)}>{t("hn.delete")}</button>
                      </>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
        <p className="nx-muted" style={{ marginBottom: 0 }}>{t("hn.note")}{system.length > 0 && <> {t("hn.system", { names: system.map((i) => i.name).join(", ") })}</>}</p>
        {changedHere && (
          <div className="nx-fa">
            <span className="nx-fa-l">{t("hn.draft")}</span>
            <button type="button" className="nx-btn" onClick={() => setDraft(null)} disabled={busy}>{t("hn.discard")}</button>
            <button type="button" className="nx-btn nx-btn--primary" onClick={review} disabled={busy}>{t("hn.review")}</button>
          </div>
        )}
      </Card>

      {editing && (
        <InterfaceDrawer
          initial={editing.value}
          isNew={editing.index === null}
          candidates={candidates.filter((c) => c.name !== nameOf(editing.value))}
          taken={taken(editing.index)}
          names={new Set([...(state.interfaces || []).filter((i) => i.configured_by !== "hyperlite").map((i) => i.name), ...managed.filter((_, k) => k !== editing.index).map(nameOf)])}
          onCancel={() => setEditing(null)}
          onSave={(v) => save(v, editing.index)}
        />
      )}

      <SideDrawer
        open={!!preview}
        title={t("hn.reviewTitle")}
        onClose={() => setPreview(null)}
        busy={busy}
        footer={<>
          <button type="button" className="nx-btn" onClick={() => setPreview(null)} disabled={busy}>{t("action.cancel")}</button>
          <button type="button" className="nx-btn nx-btn--primary" onClick={apply} disabled={busy || !preview?.changes?.length}>{t("hn.apply")}</button>
        </>}
      >
        {preview && (
          <>
            <p style={{ marginTop: 0 }}>{preview.changes.length ? t("hn.restarts", { names: preview.changes.join(", ") }) : t("hn.nothing")}</p>
            {preview.diff && <pre className="nx-code" aria-label={t("hn.diff")} style={{ whiteSpace: "pre", overflowX: "auto", maxHeight: "45vh" }}>{preview.diff}</pre>}
            <Field label={t("hn.delay")} hint={t("hn.delayHint")}>
              {(p) => <select {...p} className="nx-inp" value={delay} onChange={(e) => setDelay(Number(e.target.value))}>{DELAYS.map((d) => <option key={d} value={d}>{t("hn.seconds", { n: d })}</option>)}</select>}
            </Field>
          </>
        )}
      </SideDrawer>
    </>
  );
}

function InterfaceDrawer({ initial, isNew, candidates, taken, names, onCancel, onSave }) {
  const t = useT();
  const [v, setV] = useState(initial);
  const [tried, setTried] = useState(false);
  const set = (k) => (e) => setV({ ...v, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const toggle = (k, name) => setV({ ...v, [k]: v[k].includes(name) ? v[k].filter((x) => x !== name) : [...v[k], name] });
  const mode = v.address === "manual" || v.address === "dhcp" ? v.address : "static";

  const errors = {};
  if (v.type !== "vlan") {
    if (!NAME.test(v.name)) errors.name = t("hn.errName");
    else if (names.has(v.name)) errors.name = t("hn.errTaken");
  } else {
    if (!v.raw_device) errors.raw_device = t("hn.errRaw");
    const vid = Number(v.vid);
    if (!Number.isInteger(vid) || vid < 1 || vid > 4094) errors.vid = t("hn.errVid");
    else if (names.has(`${v.raw_device}.${vid}`)) errors.vid = t("hn.errTaken");
  }
  if (v.type === "bond" && v.slaves.length === 0) errors.slaves = t("hn.errSlaves");
  if (mode === "static" && !CIDR.test(v.address)) errors.address = t("hn.errCidr");
  if (v.gateway && (mode !== "static" || !IP.test(v.gateway))) errors.gateway = t("hn.errGateway");
  if (v.mtu && (!Number.isInteger(Number(v.mtu)) || Number(v.mtu) < 576 || Number(v.mtu) > 9216)) errors.mtu = t("hn.errMtu");
  const err = (k) => (tried ? errors[k] : undefined);

  function submit(e) {
    e.preventDefault();
    setTried(true);
    if (Object.keys(errors).length) return;
    onSave({ ...v, vid: v.type === "vlan" ? Number(v.vid) : undefined, mtu: v.mtu ? Number(v.mtu) : null, gateway: v.gateway || null });
  }
  // A bridge takes NICs, bonds and VLANs; a bond takes NICs only; a VLAN sits on any of them.
  const fits = v.type === "bond" ? ["physical"] : ["physical", "bond", "vlan"];
  const free = candidates.filter((c) => fits.includes(c.type) && !taken.has(c.name)).map((c) => c.name);
  const title = t(isNew ? `hn.new.${v.type}` : `hn.editTitle`, { name: nameOf(initial) });

  return (
    <SideDrawer open title={title} onClose={onCancel} footer={<>
      <button type="button" className="nx-btn" onClick={onCancel}>{t("action.cancel")}</button>
      <button type="submit" form="hn-form" className="nx-btn nx-btn--primary">{t("hn.keep")}</button>
    </>}>
      <form id="hn-form" className="nx-fg nx-fg--1" onSubmit={submit} noValidate>
        {v.type !== "vlan" && (
          <Field label={t("hn.name")} hint={t(v.type === "bridge" ? "hn.nameBridgeHint" : "hn.nameBondHint")} error={err("name")}>
            {(p) => <input {...p} className="nx-inp nx-mono" value={v.name} onChange={set("name")} disabled={!isNew} autoComplete="off" spellCheck={false} />}
          </Field>
        )}
        {v.type === "vlan" && (
          <>
            <Field label={t("hn.raw")} error={err("raw_device")}>
              {(p) => <select {...p} className="nx-inp" value={v.raw_device} onChange={set("raw_device")} disabled={!isNew}><option value="">—</option>{candidates.filter((c) => c.type !== "vlan").map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</select>}
            </Field>
            <Field label={t("hn.vid")} hint={t("hn.vidHint")} error={err("vid")}>
              {(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" value={v.vid} onChange={set("vid")} disabled={!isNew} />}
            </Field>
          </>
        )}
        {(v.type === "bridge" || v.type === "bond") && (
          <fieldset className="nx-f" style={{ border: 0, padding: 0, margin: 0 }} aria-describedby="hn-members-h">
            <legend style={{ fontSize: "var(--fs-13)", fontWeight: 500, marginBottom: "0.3333rem" }}>{t(v.type === "bridge" ? "hn.ports" : "hn.slaves")}</legend>
            {free.length === 0 && v[v.type === "bridge" ? "ports" : "slaves"].length === 0 ? <span className="nx-muted">{t("hn.noFree")}</span> : (
              [...new Set([...free, ...v[v.type === "bridge" ? "ports" : "slaves"]])].sort().map((c) => (
                <label key={c} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                  <input type="checkbox" checked={v[v.type === "bridge" ? "ports" : "slaves"].includes(c)} onChange={() => toggle(v.type === "bridge" ? "ports" : "slaves", c)} />
                  <span className="nx-mono">{c}</span>
                </label>
              ))
            )}
            <span id="hn-members-h" className={`nx-f-h${err("slaves") ? " is-error" : ""}`}>{err("slaves") || t(v.type === "bridge" ? "hn.portsHint" : "hn.slavesHint")}</span>
          </fieldset>
        )}
        {v.type === "bond" && (
          <>
            <Field label={t("hn.mode")} hint={t(`hn.modeHint.${v.mode}`, {})}>
              {(p) => <select {...p} className="nx-inp" value={v.mode} onChange={set("mode")}>{BOND_MODES.map((m) => <option key={m} value={m}>{m}</option>)}</select>}
            </Field>
            {HASHED.includes(v.mode) && (
              <Field label={t("hn.hash")}>
                {(p) => <select {...p} className="nx-inp" value={v.hash_policy} onChange={set("hash_policy")}>{["layer2", "layer2+3", "layer3+4"].map((m) => <option key={m} value={m}>{m}</option>)}</select>}
              </Field>
            )}
          </>
        )}
        <Field label={t("hn.addrMode")}>
          {(p) => <select {...p} className="nx-inp" value={mode} onChange={(e) => setV({ ...v, address: e.target.value === "static" ? "" : e.target.value, gateway: e.target.value === "static" ? v.gateway : "" })}>
            <option value="manual">{t("hn.addrNone")}</option><option value="dhcp">DHCP</option><option value="static">{t("hn.addrStatic")}</option>
          </select>}
        </Field>
        {mode === "static" && (
          <>
            <Field label={t("hn.address")} hint={t("hn.addressHint")} error={err("address")}>
              {(p) => <input {...p} className="nx-inp nx-mono" value={v.address} onChange={set("address")} placeholder="10.0.20.2/24" autoComplete="off" />}
            </Field>
            <Field label={t("hn.gateway")} hint={t("hn.gatewayHint")} error={err("gateway")}>
              {(p) => <input {...p} className="nx-inp nx-mono" value={v.gateway || ""} onChange={set("gateway")} autoComplete="off" />}
            </Field>
          </>
        )}
        <Field label="MTU" hint={t("hn.mtuHint")} error={err("mtu")}>
          {(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" value={v.mtu ?? ""} onChange={set("mtu")} placeholder="1500" />}
        </Field>
        <label style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <input type="checkbox" checked={v.autostart !== false} onChange={set("autostart")} />{t("hn.autostart")}
        </label>
      </form>
    </SideDrawer>
  );
}
