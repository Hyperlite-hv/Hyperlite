import { useT } from "../i18n";
import { Field } from "./ui";

// Retention of a backup schedule: how many recent backups to keep, plus (optional) the newest of the last days,
// weeks and months, the grandfather-father-son policy. An empty period field does not use that period.
export const RETENTION_LIMITS = { retention_count: [1, 365], garder_jours: [1, 366], garder_semaines: [1, 260], garder_mois: [1, 120] };

export function retentionProblems(value) {
  const bad = {};
  for (const [key, [low, high]] of Object.entries(RETENTION_LIMITS)) {
    const raw = value[key];
    if (key !== "retention_count" && (raw === "" || raw == null)) continue;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < low || n > high) bad[key] = true;
  }
  return bad;
}

// Payload values: numbers, and null for an unused period.
export function retentionPayload(value) {
  const out = {};
  for (const key of Object.keys(RETENTION_LIMITS)) out[key] = value[key] === "" || value[key] == null ? (key === "retention_count" ? 7 : null) : Number(value[key]);
  return out;
}

export function retentionForm(row) {
  return { retention_count: row?.retention_count ?? 7, garder_jours: row?.garder_jours ?? "", garder_semaines: row?.garder_semaines ?? "", garder_mois: row?.garder_mois ?? "" };
}

export default function RetentionFields({ value, onChange, disabled }) {
  const t = useT();
  const bad = retentionProblems(value);
  const input = (key, label, unit) => (
    <Field label={label} unit={unit} error={bad[key] ? (key === "retention_count" ? t("vb.retentionRule") : t("gfs.rule", { max: RETENTION_LIMITS[key][1] })) : null}>{(p) => (
      <input {...p} aria-label={key === "retention_count" ? t("a11y.retention_backups_kept") : undefined} className="nx-inp nx-mono" type="number" min={RETENTION_LIMITS[key][0]} max={RETENTION_LIMITS[key][1]} disabled={disabled}
        placeholder={key === "retention_count" ? undefined : "—"} value={value[key]} onChange={(e) => onChange({ ...value, [key]: e.target.value })} />
    )}</Field>
  );
  return (
    <fieldset className="nx-fs">
      <legend>{t("gfs.title")}</legend>
      <div className="nx-fg">
        {input("retention_count", t("gfs.last"), t("vb.copies"))}
        {input("garder_jours", t("gfs.daily"), t("gfs.days"))}
        {input("garder_semaines", t("gfs.weekly"), t("gfs.weeks"))}
        {input("garder_mois", t("gfs.monthly"), t("gfs.months"))}
      </div>
      <p className="nx-f-h" style={{ margin: 0 }}>{t("gfs.help")}</p>
    </fieldset>
  );
}

export function describeRetention(t, row) {
  const parts = [t("gfs.sumLast", { n: row.retention_count })];
  if (row.garder_jours) parts.push(t("gfs.sumDaily", { n: row.garder_jours }));
  if (row.garder_semaines) parts.push(t("gfs.sumWeekly", { n: row.garder_semaines }));
  if (row.garder_mois) parts.push(t("gfs.sumMonthly", { n: row.garder_mois }));
  return parts.join(" · ");
}
