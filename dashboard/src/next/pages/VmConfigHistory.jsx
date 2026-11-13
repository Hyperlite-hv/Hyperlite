import { useEffect, useState } from "react";
import { History } from "lucide-react";
import { fetchVmConfigHistory, fetchVmConfigVersion } from "../../api/client";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, relativeTime } from "../lib/format";
import { Empty } from "../components/ui";
import { InlineError } from "../components/States";

const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);

// A unified diff, one line each, its sign giving its colour; the "@@" headers say where in the definition.
function Diff({ text }) {
  const lines = text.split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++"));
  return (
    <pre className="hc-diff" tabIndex={0}>{lines.map((l, i) => (
      <span key={i} data-kind={l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : undefined}>{l || " "}{"\n"}</span>
    ))}</pre>
  );
}

// VM ▸ Change history: the VM's definition after every change seen, newest first, with who acted in between.
export default function VmConfigHistoryPage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [picked, setPicked] = useState(null);
  const [version, setVersion] = useState(null);
  const [full, setFull] = useState(false);
  useEffect(() => {
    setItems(null); setPicked(null);
    fetchVmConfigHistory(vm.name, vm.node).then((r) => { const l = asList(r); setItems(l); setPicked(l[0]?.id ?? null); setError(null); }).catch((e) => { setItems([]); setError(errorMessage(e)); });
  }, [vm.name, vm.node]);
  useEffect(() => {
    if (picked == null) return;
    setVersion(null); setFull(false);
    fetchVmConfigVersion(vm.name, picked, vm.node).then(setVersion).catch((e) => setVersion({ error: errorMessage(e) }));
  }, [picked, vm.name, vm.node]);
  if (error) return <InlineError message={error} />;
  if (!items) return <p className="nx-muted">{t("loading")}</p>;
  if (!items.length) return <Empty icon={History} title={t("chg.none")} text={t("chg.noneHelp")} />;
  return (
    <>
      <p className="nx-ph-desc">{t("chg.desc")}</p>
      <div className="hc-chg">
        <ol className="hc-chg-list" aria-label={t("chg.versions")}>
          {items.map((v) => (
            <li key={v.id}>
              <button type="button" aria-current={picked === v.id || undefined} onClick={() => setPicked(v.id)}>
                <b>{v.first ? t("chg.first") : t("chg.change")}</b>
                <small>{formatDateTime(v.ts, lang)} · {relativeTime(Date.parse(v.ts), lang)}</small>
                {v.actions?.length > 0 && <small className="hc-chg-who">{[...new Set(v.actions.map((a) => a.username))].join(", ")} · {[...new Set(v.actions.map((a) => a.action))].slice(0, 3).join(", ")}</small>}
              </button>
            </li>
          ))}
        </ol>
        <section className="hc-chg-view" aria-live="polite">
          {!version ? <p className="nx-muted">{t("loading")}</p> : version.error ? <InlineError message={version.error} /> : (
            <>
              <h2>{version.previous_id ? t("chg.diffTitle") : t("chg.firstTitle")}<button type="button" className="nx-lnk" aria-pressed={full} onClick={() => setFull(!full)}>{full ? t("chg.showDiff") : t("chg.showFull")}</button></h2>
              {!version.previous_id && !full && <p className="nx-hint">{t("chg.firstHelp")}</p>}
              {full || !version.previous_id ? <pre className="hc-diff" tabIndex={0}>{version.xml}</pre> : version.diff ? <Diff text={version.diff} /> : <p className="nx-muted">{t("chg.same")}</p>}
            </>
          )}
        </section>
      </div>
    </>
  );
}
