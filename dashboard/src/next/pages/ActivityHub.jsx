import { PageHeader } from "../components/ui";
import { useT } from "../i18n";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { capabilities } from "../lib/capabilities";
import ActivityPage from "./ActivityPage";
import JournalPage from "./JournalPage";

// Activity: the tasks (operations that take time: progress, status, step log) and the audit log (who did what, the
// instant actions included), one entry of the sidebar with two tabs. Each tab is a route of its own (?tab=activity,
// ?tab=journal), so links and reloads land on it. The audit log is for administrators only.

function ActivityHub({ tab }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const admin = capabilities(useAuthStore((s) => s.role)).admin;
  const TABS = admin ? ["activity", "journal"] : ["activity"];
  const go = (id) => navigateTo("datacenter", null, id);
  const onKey = (e) => {
    const i = TABS.indexOf(tab);
    const n = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i - 1 + TABS.length) % TABS.length] : null;
    if (n) { e.preventDefault(); go(n); requestAnimationFrame(() => document.getElementById(`activity-tab-${n}`)?.focus()); }
  };
  return (
    <>
      <PageHeader title={t("nav.activity")} />
      <div className="nx-tabs nx-tabs--page" role="tablist" aria-label={t("nav.activity")} onKeyDown={onKey}>
        {TABS.map((id) => (
          <button key={id} id={`activity-tab-${id}`} type="button" role="tab" aria-selected={tab === id} aria-controls="activity-panel" tabIndex={tab === id ? 0 : -1} onClick={() => go(id)}>
            {t(`activity.tab.${id}`)}
          </button>
        ))}
      </div>
      <div id="activity-panel" role="tabpanel" aria-labelledby={`activity-tab-${tab}`} className="nx-stack">
        {tab === "journal" ? <JournalPage embedded /> : <ActivityPage embedded />}
      </div>
    </>
  );
}

export function ActivityTasksPage() {
  return <ActivityHub tab="activity" />;
}
ActivityTasksPage.ownHeader = true;

export function ActivityJournalPage() {
  return <ActivityHub tab="journal" />;
}
ActivityJournalPage.ownHeader = true;
