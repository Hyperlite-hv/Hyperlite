// The Activity page's reading of the tasks (pages/ActivityPage.jsx): what runs, what failed and was not looked at
// yet, and the history folded into the runs of one account and into days. Pure, so it is tested on its own.

export const RUNNING = ["running", "pending"];
export const isRunning = (task) => RUNNING.includes(task.status);
const when = (task) => new Date(task.started_at || task.created_at).getTime();

// A run: one account's tasks that follow each other closely (an image factory's build, a script, a group backup).
// It folds into one line from MIN_RUN tasks; a lone task, or two, stays as it is.
export const RUN_GAP_S = 120;
export const MIN_RUN = 3;

/** Rows newest first -> [{kind: "task", task} | {kind: "run", id, tasks, username, status}], same order. */
export function foldRuns(rows, gapS = RUN_GAP_S, min = MIN_RUN) {
  const out = [];
  let run = [];
  const flush = () => {
    if (run.length >= min) out.push({ kind: "run", id: `run-${run[run.length - 1].id}`, tasks: run, username: run[0].username, status: runStatus(run) });
    else run.forEach((task) => out.push({ kind: "task", task }));
    run = [];
  };
  for (const task of rows) {
    const prev = run[run.length - 1];
    if (prev && (task.username !== prev.username || (when(prev) - when(task)) / 1000 > gapS)) flush();
    run.push(task);
  }
  flush();
  return out;
}

/** A run's state: running while one of its tasks runs, failed when one failed, else succeeded. */
export function runStatus(tasks) {
  if (tasks.some(isRunning)) return "running";
  if (tasks.some((t) => t.status === "failed")) return "failed";
  return "succeeded";
}

/** The targets a run worked on, the most frequent first, at most `max`. */
export function runTargets(tasks, max = 3) {
  const n = new Map();
  for (const t of tasks) if (t.target) n.set(t.target, (n.get(t.target) || 0) + 1);
  return [...n.entries()].sort((a, b) => b[1] - a[1]).slice(0, max).map(([k]) => k);
}

const dayKey = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; };

/** Items in days, newest first: [{key, day ("today" | "yesterday" | ms), items, total, failed}]. */
export function byDay(items, now = Date.now()) {
  const today = dayKey(now);
  const yesterday = dayKey(now - 86400000);
  const days = [];
  for (const item of items) {
    const first = item.kind === "run" ? item.tasks[0] : item.task;
    const ms = when(first);
    const key = dayKey(ms);
    let day = days[days.length - 1];
    if (!day || day.key !== key) {
      day = { key, day: key === today ? "today" : key === yesterday ? "yesterday" : ms, items: [], total: 0, failed: 0 };
      days.push(day);
    }
    day.items.push(item);
    const tasks = item.kind === "run" ? item.tasks : [item.task];
    day.total += tasks.length;
    day.failed += tasks.filter((t) => t.status === "failed").length;
  }
  return days;
}

/** Seconds a finished task took, null when it did not run. */
export function seconds(task) {
  if (!task.started_at || !task.finished_at) return null;
  return Math.max(0, (new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()) / 1000);
}

/** What is left of a running task, from the median of the same type's successes among `rows` (at least 3 of them):
 * null when nothing is known, 0 when it already takes longer than usual. */
export function remaining(task, rows, now = Date.now()) {
  const past = rows.filter((r) => r.type === task.type && r.status === "succeeded").map(seconds).filter((s) => s != null).sort((a, b) => a - b);
  if (past.length < 3) return null;
  const median = past[Math.floor(past.length / 2)];
  return Math.max(0, median - (now - when(task)) / 1000);
}

/** "3 min ago" within the day, the time of day otherwise ("14:02"), or the date for another day. */
export function sinceText(iso, lang, now = Date.now()) {
  if (!iso) return "—";
  const ms = new Date(iso).getTime();
  const s = Math.round((now - ms) / 1000);
  if (dayKey(ms) === dayKey(now) && s < 3600) {
    const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    return s < 45 ? rtf.format(0, "second") : rtf.format(-Math.round(s / 60), "minute");
  }
  const opts = dayKey(ms) === dayKey(now) ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  return new Intl.DateTimeFormat(lang, opts).format(new Date(ms));
}

/** A duration for people: "0,8 s", "42 s", "3 min 05", "1 h 02". */
export function durationText(s, lang) {
  if (s == null) return "—";
  if (s < 10) return `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(s)}\u00a0s`;
  if (s < 60) return `${Math.round(s)}\u00a0s`;
  if (s < 3600) return `${Math.floor(s / 60)}\u00a0min\u00a0${String(Math.round(s % 60)).padStart(2, "0")}`;
  return `${Math.floor(s / 3600)}\u00a0h\u00a0${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}

// What this browser keeps for its viewer: the failures already looked at, the accounts hidden from the list.
// Kept in localStorage, a convenience: a private window or cleared site data starts again with everything shown.
const SEEN_KEY = "hyperlite-activity-seen";
const HIDDEN_KEY = "hyperlite-activity-hidden-accounts";
const MAX_SEEN = 500;

function readSet(key) {
  try { const v = JSON.parse(localStorage.getItem(key) || "[]"); return new Set(Array.isArray(v) ? v : []); } catch { return new Set(); }
}
function writeSet(key, set, max = Infinity) {
  try { localStorage.setItem(key, JSON.stringify([...set].slice(-max))); } catch { /* storage unavailable: kept for this page only */ }
}
export const readSeen = () => readSet(SEEN_KEY);
export const writeSeen = (set) => writeSet(SEEN_KEY, set, MAX_SEEN);
export const readHidden = () => readSet(HIDDEN_KEY);
export const writeHidden = (set) => writeSet(HIDDEN_KEY, set);

/** The accounts of the rows with their number of tasks, the busiest first. */
export function accounts(rows) {
  const n = new Map();
  for (const r of rows) { const u = r.username || "system"; n.set(u, (n.get(u) || 0) + 1); }
  return [...n.entries()].sort((a, b) => b[1] - a[1]).map(([username, count]) => ({ username, count }));
}
