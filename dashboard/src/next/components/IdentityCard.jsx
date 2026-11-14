import { useCallback, useEffect, useState } from "react";
import { fetchAclRoles, fetchCustomRoles, fetchPools, fetchVmIdentity, setVmIdentity, deleteVmIdentity } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useLangStore, useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { localizeRoles } from "../lib/apiText";
import { useAccess } from "../lib/access";
import { PerimeterPicker } from "../pages/AccessPage";
import { Card, Chip, Field } from "./ui";

// A VM's own identity (docs/design/governance.md, section 9): a delegated token the guest agent writes into the
// guest, renewed by Hyperlite, revoked with the VM.
export default function IdentityCard({ vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const nodes = useAccess((s) => s.me?.nodes) || [];
  const [id, setId] = useState(null);
  const [roles, setRoles] = useState({});
  const [pools, setPools] = useState([]);
  const [f, setF] = useState({ role: "drive", shell: "sh" });
  const [at, setAt] = useState({ resource_type: "vm", resource_id: vm.name });
  const load = useCallback(() => fetchVmIdentity(vm.name).then(setId).catch(() => setId(null)), [vm.name]);
  useEffect(() => {
    load();
    Promise.all([fetchAclRoles(), fetchCustomRoles(), fetchPools().catch(() => [])]).then(([r, c, p]) => { setRoles({ ...localizeRoles(t, r), ...Object.fromEntries(c.map((x) => [x.key, x])) }); setPools(p); }).catch(() => {});
  }, [load, t]);
  if (!id) return null;
  async function enable() {
    try { const r = await setVmIdentity(vm.name, { ...f, ...at }); pushToast({ kind: r.last_error ? "warning" : "success", title: t(r.last_error ? "vi.waiting" : "vi.on"), message: r.last_error || undefined }); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  async function disable() {
    try { await deleteVmIdentity(vm.name); load(); } catch (e) { pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("vi.title")} note={t("vi.note")}>
      {id.enabled ? (
        <>
          <p><Chip tone={id.delivered_at ? "success" : "warning"}>{t(id.delivered_at ? "vi.delivered" : "vi.notDelivered")}</Chip> {roles[id.role]?.label || id.role} · {id.resource_type === "cluster" ? t("acc.wholeCluster") : `${t(`acc.kind.${id.resource_type}`)} ${id.resource_id}`}</p>
          <p className="nx-muted">{t("vi.where", { path: id.path })} · {t("vi.until", { d: formatDateTime(id.expires_at, lang) })}{id.last_error ? ` · ${id.last_error}` : ""}</p>
          <div className="nx-fa"><button type="button" className="nx-btn nx-btn--ghost" onClick={disable}>{t("vi.off")}</button></div>
        </>
      ) : (
        <>
          <div className="nx-fg">
            <Field label={t("sec.role")}>{(p) => <select {...p} className="nx-inp" value={f.role} onChange={(e) => setF((x) => ({ ...x, role: e.target.value }))}>{Object.entries(roles).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}</select>}</Field>
            <Field label={t("vi.system")}>{(p) => <select {...p} className="nx-inp" value={f.shell} onChange={(e) => setF((x) => ({ ...x, shell: e.target.value }))}><option value="sh">Linux</option><option value="powershell">Windows</option></select>}</Field>
          </div>
          <PerimeterPicker value={at} onChange={setAt} pools={pools} nodes={nodes} />
          <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!at.resource_id} onClick={enable}>{t("vi.enable")}</button></div>
        </>
      )}
    </Card>
  );
}
