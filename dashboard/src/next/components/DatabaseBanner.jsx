import { useState } from "react";
import { checkHostDatabase, repairHostDatabase } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";

// This node's database, when it is damaged or a background loop keeps failing on it (app/core/db_health.py): said
// above every page, with the repair that fits. Only an index whose entries are wrong is repaired from here; the other
// repairs need the service stopped, so the banner names the command.
export default function DatabaseBanner({ db, admin }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const [busy, setBusy] = useState(false);

  async function run(action, done) {
    setBusy(true);
    try {
      const after = await action();
      pushToast(after?.status === "ok"
        ? { kind: "success", title: t(done), message: t("db.soundNow") }
        : { kind: "error", title: t("db.stillDamaged"), message: t(`db.repair.${after?.repair || "restore"}`) });
      await refreshAll();
    } catch (e) {
      pushToast({ kind: "error", title: t("db.failed"), message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  const listed = (db.tables?.length ? db.tables : db.indexes || []).join(", ");
  const named = listed ? ` (${listed})` : "";
  const text = db.status === "failing"
    ? t("db.failing", { loops: Object.keys(db.loops || {}).join(", ") })
    : t(`db.kind.${db.kind}`, { names: named });
  return (
    <div className="nx-banner nx-banner--danger" role="alert">
      <span style={{ flex: 1 }}>
        <b>{t("db.title")}</b> {text} {db.status === "damaged" && t(`db.repair.${db.repair}`)}
      </span>
      {admin && (db.repair === "reindex"
        ? <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={() => run(repairHostDatabase, "db.repaired")}>{t("db.repairNow")}</button>
        : <button type="button" className="nx-btn nx-btn--sm" disabled={busy} onClick={() => run(checkHostDatabase, "db.checked")}>{t("db.checkNow")}</button>)}
    </div>
  );
}
